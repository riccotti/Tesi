"""
Functions for explaining classifiers that use Image data.
"""
import copy

import numpy as np
import sklearn
import sklearn.preprocessing
from sklearn.utils import check_random_state
from skimage.color import gray2rgb

try: 
    from . import lime_base
    from .wrappers.scikit_image import SegmentationAlgorithm
except:
    import lime_base
    from wrappers.scikit_image import SegmentationAlgorithm


class ImageExplanation(object):
    def __init__(self, image, segments):
        """Init function.

        Args:
            image: 3d numpy array
            segments: 2d numpy array, with the output from skimage.segmentation
        """
        self.image = image
        self.segments = segments
        self.intercept = {}
        self.local_exp = {}
        self.local_pred = None

    def get_image_and_mask(self, label, positive_only=True, hide_rest=False,
                           num_features=5, min_weight=0.):
        """Init function.

        Args:
            label: label to explain
            positive_only: if True, only take superpixels that contribute to
                the prediction of the label. Otherwise, use the top
                num_features superpixels, which can be positive or negative
                towards the label
            hide_rest: if True, make the non-explanation part of the return
                image gray
            num_features: number of superpixels to include in explanation
            min_weight: TODO

        Returns:
            (image, mask), where image is a 3d numpy array and mask is a 2d
            numpy array that can be used with
            skimage.segmentation.mark_boundaries
        """
        if label not in self.local_exp:
            raise KeyError('Label not in explanation')
        segments = self.segments
        image = self.image
        exp = self.local_exp[label]
        mask = np.zeros(segments.shape, segments.dtype)
        if hide_rest:
            temp = np.zeros(self.image.shape)
        else:
            temp = self.image.copy()
        if positive_only:
            fs = [x[0] for x in exp
                  if x[1] > 0 and x[1] > min_weight][:num_features]
            for f in fs:
                temp[segments == f] = image[segments == f].copy()
                
                # Light up the green channel (1) of the image
                temp[segments == f, 1] = np.max(image)
                
                mask[segments == f] = 1
            return temp, mask
        else:
            for f, w in exp[:num_features]:
                if np.abs(w) < min_weight:
                    continue
                c = 0 if w < 0 else 1
                mask[segments == f] = 1 if w < 0 else 2
                temp[segments == f] = image[segments == f].copy()
                
                # Here is where the red and green areas are set
                temp[segments == f, c] = np.max(image)
                
                for cp in [0, 1, 2]:
                    if c == cp:
                        continue
                    # temp[segments == f, cp] *= 0.5
            return temp, mask


class LimeImageExplainer(object):
    """Explains predictions on Image (i.e. matrix) data.
    For numerical features, perturb them by sampling from a Normal(0,1) and
    doing the inverse operation of mean-centering and scaling, according to the
    means and stds in the training data. For categorical features, perturb by
    sampling according to the training distribution, and making a binary
    feature that is 1 when the value is the same as the instance being
    explained."""

    def __init__(self, kernel_width=.25, verbose=False,
                 feature_selection='auto', random_state=None):
        """Init function.

        Args:
            kernel_width: kernel width for the exponential kernel.
            If None, defaults to sqrt(number of columns) * 0.75
            verbose: if true, print local prediction values from linear model
            feature_selection: feature selection method. can be
                'forward_selection', 'lasso_path', 'none' or 'auto'.
                See function 'explain_instance_with_data' in lime_base.py for
                details on what each of the options does.
            random_state: an integer or numpy.RandomState that will be used to
                generate random numbers. If None, the random state will be
                initialized using the internal numpy seed.
        """
        kernel_width = float(kernel_width)

        def kernel(d):
            return np.sqrt(np.exp(-(d ** 2) / kernel_width ** 2))

        self.random_state = check_random_state(random_state)
        self.feature_selection = feature_selection
        self.base = lime_base.LimeBase(kernel, verbose, random_state=self.random_state)

    def explain_instance(self, image, classifier_fn, labels=(1,),
                         hide_color=None,
                         top_labels=5, num_features=100000, num_samples=1000,
                         batch_size=10,
                         segmentation_fn=None,
                         distance_metric='cosine',
                         model_regressor=None,
                         random_seed=None,
                         return_sample_neighborhood_images=False):
        """Generates explanations for a prediction.

        First, we generate neighborhood data by randomly perturbing features
        from the instance (see __data_inverse). We then learn locally weighted
        linear models on this neighborhood data to explain each of the classes
        in an interpretable way (see lime_base.py).

        Args:
            image: 3 dimension RGB image. If this is only two dimensional,
                we will assume it's a grayscale image and call gray2rgb.
            classifier_fn: classifier prediction probability function, which
                takes a numpy array and outputs prediction probabilities.  For
                ScikitClassifiers , this is classifier.predict_proba.
            labels: iterable with labels to be explained.
            hide_color: TODO
            top_labels: if not None, ignore labels and produce explanations for
                the K labels with highest prediction probabilities, where K is
                this parameter.
            num_features: maximum number of features present in explanation
            num_samples: size of the neighborhood to learn the linear model
            batch_size: TODO
            distance_metric: the distance metric to use for weights.
            model_regressor: sklearn regressor to use in explanation. Defaults
            to Ridge regression in LimeBase. Must have model_regressor.coef_
            and 'sample_weight' as a parameter to model_regressor.fit()
            segmentation_fn: SegmentationAlgorithm, wrapped skimage
            segmentation function
            random_seed: integer used as random seed for the segmentation
                algorithm. If None, a random integer, between 0 and 1000,
                will be generated using the internal random number generator.

        Returns:
            An Explanation object (see explanation.py) with the corresponding
            explanations.
        """
        if len(image.shape) == 2:
            image = gray2rgb(image)
        if random_seed is None:
            random_seed = self.random_state.randint(0, high=1000)

        if segmentation_fn is None:
            segmentation_fn = SegmentationAlgorithm('quickshift', kernel_size=4,
                                                    max_dist=200, ratio=0.2,
                                                    random_seed=random_seed)
        try:
            segments = segmentation_fn(image)
        except ValueError as e:
            raise e

        fudged_image = image.copy()
        if hide_color is None:
            for x in np.unique(segments):
                fudged_image[segments == x] = (
                    np.mean(image[segments == x][:, 0]),
                    np.mean(image[segments == x][:, 1]),
                    np.mean(image[segments == x][:, 2]))
        else:
            fudged_image[:] = hide_color

        top = labels

        if return_sample_neighborhood_images:
            data, labels, sam = self.data_labels(image, fudged_image, segments,
            		                                    classifier_fn, num_samples,
            		                                    batch_size=batch_size,
            		                                    return_sample_neighborhood_images=return_sample_neighborhood_images)
        else:
            data, labels = self.data_labels(image, fudged_image, segments,
            		                                    classifier_fn, num_samples,
            		                                    batch_size=batch_size,
            		                                    return_sample_neighborhood_images=return_sample_neighborhood_images)

        distances = sklearn.metrics.pairwise_distances(
            data,
            data[0].reshape(1, -1),
            metric=distance_metric
        ).ravel()
        
        ret_exp = ImageExplanation(image, segments)

        if top_labels:
            top = np.argsort(labels[0])[-top_labels:]
            ret_exp.top_labels = list(top)
            ret_exp.top_labels.reverse()

        for label in top:
            (ret_exp.intercept[label],
             ret_exp.local_exp[label],
             ret_exp.score, ret_exp.local_pred) = self.base.explain_instance_with_data(
                data, labels, distances, label, num_features,
                model_regressor=model_regressor,
                feature_selection=self.feature_selection)
        if(return_sample_neighborhood_images):
            return ret_exp, sam
        return ret_exp

    def data_labels(self,
                    image,
                    fudged_image,
                    segments,
                    classifier_fn,
                    num_samples,
                    batch_size=10,
                    return_sample_neighborhood_images=False,
                    fudged_images_pool=[]):
        """Generates images and predictions in the neighborhood of this image.

        Args:
            image: 3d numpy array, the image
            fudged_image: 3d numpy array, image to replace original image when
                superpixel is turned off
            segments: segmentation of the image
            classifier_fn: function that takes a list of images and returns a
                matrix of prediction probabilities
            num_samples: size of the neighborhood to learn the linear model
            batch_size: classifier_fn will be called on batches of this size.

        Returns:
            A tuple (data, labels), where:
                data: dense num_samples * num_superpixels
                labels: prediction probabilities matrix
        """
        import random
        
        if len(fudged_images_pool) == 0:
            fudged_images_pool = [fudged_image]
        
        n_features = np.unique(segments).shape[0]
        data = self.random_state.randint(0, 2, num_samples * n_features)\
            .reshape((num_samples, n_features))
        labels = []
        data[0, :] = 1
        imgs = []
        samples = []
        
        for row in data:
            temp = copy.deepcopy(image)
            zeros = np.where(row == 0)[0]
            mask = np.zeros(segments.shape)#.astype(bool)
            
            fudged_images_indexes = range(1,len(fudged_images_pool)+1)
            for z in zeros:
                val = random.choice(fudged_images_indexes)
                mask[segments == z] = val
            
            for i in fudged_images_indexes:
                temp[mask == i] = fudged_images_pool[i-1][mask == i]
                
            imgs.append(temp)
            samples = imgs
            
            if len(imgs) == batch_size:
                preds = classifier_fn(np.array(imgs))
                labels.extend(preds)
                #if(return_sample_neighborhood_images):
                
                imgs = []
        if len(imgs) > 0:
            preds = classifier_fn(np.array(imgs))
            labels.extend(preds)

        if(return_sample_neighborhood_images):
            return data, np.array(labels), samples
        else:
            return data, np.array(labels)

class LimeImageMixedPatchworkExplainer(LimeImageExplainer): 
    
    # Extend LimeImageExplainer so that  three new parameters are added. These
    # parameters holds i) images of the same cluster, ii) all other images and
    # iii) the probability to draw images from the same cluster.
    def __init__(self, same_clus_images, all_other_images, same_clus_prob, *args, **kwargs):
        super(LimeImageMixedPatchworkExplainer, self).__init__(*args, **kwargs)
        self.same_clus_images = same_clus_images
        self.all_other_images = all_other_images
        self.same_clus_prob = same_clus_prob
        
    # Override
    def explain_instance(self, image, classifier_fn, labels=(1,),
                         hide_color=None,
                         top_labels=5, num_features=100000, num_samples=1000,
                         batch_size=10,
                         segmentation_fn=None,
                         distance_metric='cosine',
                         model_regressor=None,
                         random_seed=None,
                         return_sample_neighborhood_images=False):
        
        if len(image.shape) == 2:
            image = gray2rgb(image)
        if random_seed is None:
            random_seed = self.random_state.randint(0, high=1000)

        if segmentation_fn is None:
            segmentation_fn = SegmentationAlgorithm('quickshift', kernel_size=4,
                                                    max_dist=200, ratio=0.2,
                                                    random_seed=random_seed)
        try:
            segments = segmentation_fn(image)
        except ValueError as e:
            raise e

        # Draw the fudged_image at random
        #import random
        fudged_image = None#random.choice(self.image_pool)

        top = labels

        if(return_sample_neighborhood_images):
            data, labels, samples = self.data_labels(image, fudged_image, segments,
                                                     classifier_fn, num_samples,
                                                     batch_size=batch_size, 
                                                     return_sample_neighborhood_images=return_sample_neighborhood_images,
                                                     )
        else:
            data, labels = self.data_labels(image, fudged_image, segments,
                                            classifier_fn, num_samples,
                                            batch_size=batch_size, 
                                            return_sample_neighborhood_images=return_sample_neighborhood_images,
                                            )

        distances = sklearn.metrics.pairwise_distances(
            data,
            data[0].reshape(1, -1),
            metric=distance_metric
        ).ravel()
    
        ret_exp = ImageExplanation(image, segments)

        if top_labels:
            top = np.argsort(labels[0])[-top_labels:]
            ret_exp.top_labels = list(top)
            ret_exp.top_labels.reverse()
            
        for label in top:
            (ret_exp.intercept[label],
             ret_exp.local_exp[label],
             ret_exp.score, ret_exp.local_pred) = self.base.explain_instance_with_data(
                data, labels, distances, label, num_features,
                model_regressor=model_regressor,
                feature_selection=self.feature_selection)

        if(return_sample_neighborhood_images):             
            return ret_exp, samples
        else:
            return ret_exp
        
    # Override
    def data_labels(self,
                    image,
                    fudged_image,
                    segments,
                    classifier_fn,
                    num_samples,
                    batch_size=10,
                    return_sample_neighborhood_images=False):
        
        import random as rnd
        
        n_features = np.unique(segments).shape[0]
        data = self.random_state.randint(0, 2, num_samples * n_features)\
            .reshape((num_samples, n_features))
        labels = []
        data[0, :] = 1
        imgs = []
        samples = []
        
        for row in data:
            temp = copy.deepcopy(image)
            zeros = np.where(row == 0)[0]
            mask = np.zeros(segments.shape)#.astype(bool)
            
            all_other_images_indexes = range(1,len(self.all_other_images)+1)
            same_clus_images_indexes = range(1,len(self.same_clus_images)+1)
            
            for z in zeros:
                proba = rnd.random()
                if proba < self.same_clus_prob:
                    val = - rnd.choice(same_clus_images_indexes)
                else:
                    val = rnd.choice(all_other_images_indexes)
                    
                mask[segments == z] = val
            
            for i in all_other_images_indexes:
                temp[mask == i] = self.all_other_images[i-1][mask == i]
            
            for j in same_clus_images_indexes:
                temp[mask == -j] = self.same_clus_images[j-1][mask == -j]
                
                
            imgs.append(temp)
            samples = imgs
            
            if len(imgs) == batch_size:
                preds = classifier_fn(np.array(imgs))
                labels.extend(preds)
                imgs = []
                
        if len(imgs) > 0:
            preds = classifier_fn(np.array(imgs))
            labels.extend(preds)

        if(return_sample_neighborhood_images):
            return data, np.array(labels), samples
        else:
            return data, np.array(labels)
    

class LimeImagePatchworkExplainer(LimeImageExplainer): 

    # Extend LimeImageExplainer so that  a new parameter is added. This
    # parameter holds the collection of images to draw the fudged image
    # (i.e. image to show when superpixel is turned off) from.
    def __init__(self, image_pool, *args, **kwargs):
        super(LimeImagePatchworkExplainer, self).__init__(*args, **kwargs)
        self.image_pool = image_pool

    # LimeImagePatchworkExplainer  ovverides the explain_instance method 
    # so that the fudged_image passed to the method data_labels is drawn 
    # at random from the ones inside 'image_pool'.
    def explain_instance(self, image, classifier_fn, labels=(1,),
                         hide_color=None,
                         top_labels=5, num_features=100000, num_samples=1000,
                         batch_size=10,
                         segmentation_fn=None,
                         distance_metric='cosine',
                         model_regressor=None,
                         random_seed=None,
                         return_sample_neighborhood_images=False):
        """Generates explanations for a prediction.

        First, we generate neighborhood data by randomly perturbing features
        from the instance (see __data_inverse). We then learn locally weighted
        linear models on this neighborhood data to explain each of the classes
        in an interpretable way (see lime_base.py).

        Args:
            image: 3 dimension RGB image. If this is only two dimensional,
                we will assume it's a grayscale image and call gray2rgb.
            classifier_fn: classifier prediction probability function, which
                takes a numpy array and outputs prediction probabilities.  For
                ScikitClassifiers , this is classifier.predict_proba.
            labels: iterable with labels to be explained.
            hide_color: TODO
            top_labels: if not None, ignore labels and produce explanations for
                the K labels with highest prediction probabilities, where K is
                this parameter.
            num_features: maximum number of features present in explanation
            num_samples: size of the neighborhood to learn the linear model
            batch_size: TODO
            distance_metric: the distance metric to use for weights.
            model_regressor: sklearn regressor to use in explanation. Defaults
            to Ridge regression in LimeBase. Must have model_regressor.coef_
            and 'sample_weight' as a parameter to model_regressor.fit()
            segmentation_fn: SegmentationAlgorithm, wrapped skimage
            segmentation function
            random_seed: integer used as random seed for the segmentation
                algorithm. If None, a random integer, between 0 and 1000,
                will be generated using the internal random number generator.

        Returns:
            An Explanation object (see explanation.py) with the corresponding
            explanations.
        """
        if len(image.shape) == 2:
            image = gray2rgb(image)
        if random_seed is None:
            random_seed = self.random_state.randint(0, high=1000)

        if segmentation_fn is None:
            segmentation_fn = SegmentationAlgorithm('quickshift', kernel_size=4,
                                                    max_dist=200, ratio=0.2,
                                                    random_seed=random_seed)
        try:
            segments = segmentation_fn(image)
        except ValueError as e:
            raise e

        # fudged_image = image.copy()
        # if hide_color is None:
        #     for x in np.unique(segments):
        #         fudged_image[segments == x] = (
        #             np.mean(image[segments == x][:, 0]),
        #             np.mean(image[segments == x][:, 1]),
        #             np.mean(image[segments == x][:, 2]))
        # else:
        #     fudged_image[:] = hide_color

        # Draw the fudged_image at random
        import random
        fudged_image = random.choice(self.image_pool)

        top = labels

        if(return_sample_neighborhood_images):
            data, labels, samples = self.data_labels(image, fudged_image, segments,
                                                     classifier_fn, num_samples,
                                                     batch_size=batch_size, 
                                                     return_sample_neighborhood_images=return_sample_neighborhood_images,
                                                     fudged_images_pool=self.image_pool)
        else:
            data, labels = self.data_labels(image, fudged_image, segments,
                                            classifier_fn, num_samples,
                                            batch_size=batch_size, 
                                            return_sample_neighborhood_images=return_sample_neighborhood_images,
                                            fudged_images_pool=self.image_pool)

        distances = sklearn.metrics.pairwise_distances(
            data,
            data[0].reshape(1, -1),
            metric=distance_metric
        ).ravel()
    
        ret_exp = ImageExplanation(image, segments)

        if top_labels:
            top = np.argsort(labels[0])[-top_labels:]
            ret_exp.top_labels = list(top)
            ret_exp.top_labels.reverse()
            
        for label in top:
            (ret_exp.intercept[label],
             ret_exp.local_exp[label],
             ret_exp.score, ret_exp.local_pred) = self.base.explain_instance_with_data(
                data, labels, distances, label, num_features,
                model_regressor=model_regressor,
                feature_selection=self.feature_selection)

        if(return_sample_neighborhood_images):             
            return ret_exp, samples
        else:
            return ret_exp
    
        
class LimeImageEnhancedPatchworkExplainer(LimeImagePatchworkExplainer):
    
    def __init__(self, image_pool, *args, **kwargs):
        super(LimeImageEnhancedPatchworkExplainer, self).__init__(image_pool,*args, **kwargs)
        self.image_pool = image_pool
        
    # Overrides
    def data_labels(self,
                    image,
                    fudged_image,
                    segments,
                    classifier_fn,
                    num_samples,
                    batch_size=10,
                    return_sample_neighborhood_images=False,
                    fudged_images_pool=[]):
        
        if len(fudged_images_pool) == 0:
            fudged_images_pool = [fudged_image]
        
        n_features = np.unique(segments).shape[0]
        data = self.random_state.randint(0, 2, num_samples * n_features)\
            .reshape((num_samples, n_features))
        labels = []
        data[0, :] = 1
        imgs = []
        samples = []
            
        patch_wall = copy.deepcopy(image)
        
#        from multiprocessing import Pool, cpu_count
#        
#        import time
#        start = time.time()
#        
#        pool = Pool(processes=cpu_count())
#        patch_pairs = [pool.apply_async(loop_body, 
#                                         (segments, 
#                                          fudged_images_pool, 
#                                          seg, 
#                                          image)) 
#                        for seg in np.unique(segments)]
#        pool.close()
#        pairs = [res.get(timeout=100) for res in patch_pairs]
#        pool.join()
#        del pool
#        
#        for pai in pairs:
#            patch = pai[0]
#            numb = pai[1]
#            patch_wall[segments == numb] = patch
        
        for seg in np.unique(segments):
            patches = self.extract_patches(fudged_images_pool, segments, seg)
            patch_wall[segments == seg] = \
                self.get_most_similar_patch(patch_wall[segments == seg], patches)
                
#        end = time.time()
#        print "patch wall created in %s" % str(end-start)
        
        for row in data:
            temp = copy.deepcopy(image)
            zeros = np.where(row == 0)[0]
            #mask = np.zeros(segments.shape)#.astype(bool)
            
            for z in zeros:
                temp[segments == z] = patch_wall[segments == z]
                
            imgs.append(temp)
            samples = imgs
            
            if len(imgs) == batch_size:
                preds = classifier_fn(np.array(imgs))
                labels.extend(preds)
                imgs = []
                
        if len(imgs) > 0:
            preds = classifier_fn(np.array(imgs))
            labels.extend(preds)

        if(return_sample_neighborhood_images):
            return data, np.array(labels), samples
        else:
            return data, np.array(labels)
    
    
    
    def extract_patches(self, images, segments, segment_number):
        patches = []
        for im in images:
            patch = im[segments == segment_number]
            patches.append(patch)
        return patches
    
    def get_most_similar_patch(self, reference_patch, all_patches):
        best_patch = None
        min_error = float('inf')
        for p in all_patches:
            error = np.linalg.norm(reference_patch - p)
            if error < min_error and error > 0.0:
                min_error = error
                best_patch = p
        return best_patch

#def loop_body(segments, images, seg_no, original_image):
#    patches = extract_patches(images, segments, seg_no, original_image)
#    patch = get_most_similar_patch(original_image[segments == seg_no], patches)
#    return (patch, seg_no)