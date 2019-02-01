"""
class IRAPScore
class IRAPSClassifier
class BinarizeTargetClassifier
class _BinarizeTargetScorer
class _BinarizeTargetProbaScorer

binarize_auc_scorer
binarize_average_precision_scorer

binarize_accuracy_scorer
binarize_balanced_accuracy_scorer
binarize_precision_scorer
binarize_recall_scorer
"""


import numpy as np
import random
import warnings

from abc import ABCMeta
from scipy.stats import ttest_ind
from sklearn import metrics
from sklearn.base import BaseEstimator, RegressorMixin
from sklearn.externals import joblib, six
from sklearn.feature_selection.univariate_selection import _BaseFilter
from sklearn.metrics.scorer import _BaseScorer
from sklearn.model_selection._split import _BaseKFold
from sklearn.pipeline import Pipeline
from sklearn.utils import as_float_array, check_random_state, check_X_y
from sklearn.utils.validation import _num_samples, check_array, check_is_fitted, column_or_1d


VERSION = '0.1.1'


class IRAPSCore(six.with_metaclass(ABCMeta, BaseEstimator)):
    """
    Base class of IRAPSClassifier
    From sklearn BaseEstimator:
        get_params()
        set_params()
    """
    def __init__(self, n_iter=1000, positive_thres=-1,
                negative_thres=0, verbose=0, random_state=None):
        """
        IRAPS turns towwards general Anomaly Detection
        It comapares positive_thres with negative_thres,
        and decide which portion is the positive target.
        e.g.:
        (positive_thres=-1, negative_thres=0) => positive = Z_score of target < -1
        (positive_thres=1, negative_thres=0) => positive = Z_score of target > 1

        Note: The positive targets here is always the abnormal minority group.
        """
        self.n_iter = n_iter
        self.positive_thres = positive_thres
        self.negative_thres = negative_thres
        self.verbose = verbose
        self.random_state = random_state

    def fit(self, X, y):
        """
        X: array-like (n_samples x n_features)
        y: 1-d array-like (n_samples)
        """
        X, y = check_X_y(X, y, ['csr', 'csc'], multi_output=False)
        #each iteration select a random number of random subset of training samples
        # this is somewhat different from the original IRAPS method, but effect is almost the same.
        SAMPLE_SIZE = [0.25, 0.75]
        n_samples = X.shape[0]
        pvalues = None
        fold_changes = None
        base_values = None

        i = 0
        seed = self.random_state if self.random_state else 0
        max_try = seed + 2000
        while i < self.n_iter:
            ## TODO: support more random_state/seed
            if seed > max_try:
                if i < 50:
                    raise Exception("Max tries reached, too few (%d) valid feature lists were generated!" %i)
                else:
                    warnings.warn("Max tries readched, %d valid feature lists were generated!" %i)
                    break
            if self.random_state is None:
                n_select = random.randint(int(n_samples*SAMPLE_SIZE[0]), int(n_samples*SAMPLE_SIZE[1]))
                index = random.sample(list(range(n_samples)), n_select)
            else:
                n_select = random.Random(seed).randint(int(n_samples*SAMPLE_SIZE[0]), int(n_samples*SAMPLE_SIZE[1]))
                index = random.Random(seed).sample(list(range(n_samples)), n_select)
            seed += 1
            X_selected, y_selected = X[index], y[index]

            # Spliting by z_scores.
            y_selected = (y_selected - y_selected.mean())/y_selected.std()
            if self.positive_thres < self.negative_thres:
                X_selected_positive = X_selected[y_selected < self.positive_thres]
                X_selected_negative = X_selected[y_selected > self.negative_thres]
            else:
                X_selected_positive = X_selected[y_selected > self.positive_thres]
                X_selected_negative = X_selected[y_selected < self.negative_thres]

            # For every iteration, at least 5 responders are selected
            if X_selected_positive.shape[0] < 5:
                if self.random_state is not None:
                    raise Exception("Error: fewer than 5 positives were selected while random_state is not None!")
                continue

            if self.verbose:
                print("Working on iteration %d/%d, %s/%d samples were positive/selected."\
                        %(i+1, self.n_iter, X_selected_positive.shape[0], n_select))
            i += 1

            # p_values
            _, p = ttest_ind(X_selected_positive, X_selected_negative, axis=0, equal_var=False)
            if pvalues is None:
                pvalues = p
            else:
                pvalues = np.vstack((pvalues, p))

            # fold_change == mean change?
            # TODO implement other normalization method
            positive_mean = X_selected_positive.mean(axis=0)
            negative_mean = X_selected_negative.mean(axis=0)
            mean_change = positive_mean - negative_mean
            #mean_change = np.select([positive_mean > negative_mean, positive_mean < negative_mean],
            #                        [positive_mean / negative_mean, -negative_mean / positive_mean])
            # mean_change could be adjusted by power of 2
            # mean_change = 2**mean_change if mean_change>0 else -2**abs(mean_change)
            if fold_changes is None:
                fold_changes = mean_change
            else:
                fold_changes = np.vstack((fold_changes, mean_change))

            if base_values is None:
                base_values = negative_mean
            else:
                base_values = np.vstack((base_values, negative_mean))

        self.fold_changes_ = np.asarray(fold_changes)
        self.pvalues_ = np.asarray(pvalues)
        self.base_values_ = np.asarray(base_values)

        return self


"""
memory = joblib.Memory('./memory_cache')
class MemoryFit(object):
    def fit(self, *args, **kwargs):
        fit = memory.cache(super(MemoryFit, self).fit)
        cached_self = fit(*args, **kwargs)
        vars(self).update(vars(cached_self))
class CachedIRAPSCore(MemoryFit, IRAPSCore):
    pass
"""


class IRAPSClassifier(six.with_metaclass(ABCMeta, _BaseFilter, BaseEstimator, RegressorMixin)):
    """
    Extend the bases of both sklearn feature_selector and classifier.
    From sklearn BaseEstimator:
        get_params()
        set_params()
    From sklearn _BaseFilter:
        get_support()
        fit_transform(X)
        transform(X)
    From sklearn RegressorMixin:
        score(X, y): R2
    New:
        predict(X)
        predict_label(X)
        get_signature()
    Properties:
        discretize_value

    """
    def __init__(self, iraps_core, p_thres=1e-4, fc_thres=0.1, occurance=0.8, discretize=-1):
        self.iraps_core = iraps_core
        self.p_thres = p_thres
        self.fc_thres = fc_thres
        self.occurance = occurance
        self.discretize = discretize

    def fit(self, X, y):
        # allow pre-fitted iraps_core here
        if not hasattr(self.iraps_core, 'pvalues_'):
            self.iraps_core.fit(X, y)

        pvalues = as_float_array(self.iraps_core.pvalues_, copy=True)
        ## why np.nan is here?
        pvalues[np.isnan(pvalues)] = np.finfo(pvalues.dtype).max

        fold_changes = as_float_array(self.iraps_core.fold_changes_, copy=True)
        fold_changes[np.isnan(fold_changes)] = 0.0

        base_values = as_float_array(self.iraps_core.base_values_, copy=True)

        p_thres = self.p_thres
        fc_thres = self.fc_thres
        occurance = self.occurance

        mask_0 = np.zeros(pvalues.shape, dtype=np.int32)
        # mark p_values less than the threashold
        mask_0[pvalues <= p_thres] = 1
        # mark fold_changes only when greater than the threashold
        mask_0[abs(fold_changes) < fc_thres] = 0

        # count the occurance and mask greater than the threshold
        counts = mask_0.sum(axis=0)
        occurance_thres = int(occurance * self.iraps_core.n_iter)
        mask = np.zeros(counts.shape, dtype=bool)
        mask[counts >= occurance_thres] = 1

        # generate signature
        fold_changes[mask_0 == 0] = 0.0
        signature = fold_changes[:, mask].sum(axis=0) / counts[mask]
        signature = np.vstack((signature, base_values[:, mask].mean(axis=0)))

        self.signature_ = np.asarray(signature)
        self.mask_ = mask
        ## TODO: support other discretize method: fixed value, upper third quater, etc.
        self.discretize_value = y.mean() + y.std() * self.discretize
        if self.iraps_core.negative_thres > self.iraps_core.positive_thres:
            self.less_is_positive = True
        else:
            self.less_is_positive = False

        return self


    def _get_support_mask(self):
        """
        return mask of feature selection indices
        """
        check_is_fitted(self, 'mask_')

        return self.mask_

    def get_signature(self, min_size=1):
        """
        return signature
        """
        #TODO: implement minimum size of signature
        # It's not clearn whether min_size could impact prediction performance
        check_is_fitted(self, 'signature_')

        if self.signature_.shape[1] >= min_size:
            return self.signature_
        else:
            return None

    def predict(self, X):
        """
        compute the correlation coefficient with irpas signature
        """
        signature = self.get_signature()
        if signature is None:
            print('The classifier got None signature or the number of sinature feature is less than minimum!')
            return

        X = as_float_array(X)
        X_transformed = self.transform(X) - signature[1]
        corrcoef = np.array([np.corrcoef(signature[0], e)[0][1] for e in X_transformed])
        corrcoef[np.isnan(corrcoef)] = np.finfo(np.float32).min

        return corrcoef

    def predict_label(self, X, clf_cutoff=0.4):
        return self.predict(X) >= clf_cutoff


class OrderedKFold(_BaseKFold):
    """
    Split into K fold based on ordered target value 
    
    Parameters
    ----------
    n_splits : int, default=3
        Number of folds. Must be at least 2.
    """
    def __init__(self, n_splits=3, shuffle=False, random_state=None):
        super(OrderedKFold, self).__init__(n_splits, shuffle, random_state)

    def _iter_test_indices(self, X, y, groups=None):
        n_samples = _num_samples(X)
        n_splits = self.n_splits
        y = np.asarray(y)
        sorted_index = np.argsort(y)
        if self.shuffle:
            current = 0
            for i in range(n_samples/int(n_splits)):
                start, stop = current, current + n_splits
                check_random_state(self.random_state).shuffle(sorted_index[start:stop])
                current = stop
            check_random_state(self.random_state).shuffle(sorted_index[current:])
        
        for i in range(n_splits):
            yield sorted_index[i:n_samples:n_splits]


class BinarizeTargetClassifier(BaseEstimator, RegressorMixin):
    """
    Convert continuous target to binary labels (True and False)
    and apply a classification estimator.

    Parameters
    ----------
    classifier: object
        Estimator object such as derived from sklearn `ClassifierMixin`.

    z_score: float, default=-1.0
        Threshold value based on z_score. Will be ignored when
        fixed_value is set

    value: float, default=None
        Threshold value

    less_is_positive: boolean, default=True
        When target is less the threshold value, it will be converted
        to True, False otherwise.

    Attributes
    ----------
    classifier_: object
        Fitted classifier

    discretize_value: float
        The threshold value used to discretize True and False targets
    """
    def __init__(self, classifier, z_score=-1, value=None, less_is_positive=True):
        self.classifier = classifier
        self.z_score = z_score
        self.value = value
        self.less_is_positive = less_is_positive

    def fit(self, X, y, sample_weight=None):
        """
        Convert y to True and False labels and then fit the classifier with X and new y

        Returns
        ------
        self: object
        """
        y = check_array(y, accept_sparse=False, force_all_finite=True,
                        ensure_2d=False, dtype='numeric')
        y = column_or_1d(y)

        if self.value is None:
            discretize_value = y.mean() + y.std() * self.z_score
        else:
            discretize_value = self.Value
        self.discretize_value = discretize_value

        if self.less_is_positive:
            y_trans = y < discretize_value
        else:
            y_trans = y > discretize_value

        self.classifier_ = clone(self.classifier)
        
        if sample_weight is not None:
            self.classifier_.fit(X, y_trans, sample_weight=sample_weight)
        else:
            self.classifier_.fit(X, y_trans)

        if hasattr(self.classifier_, 'feature_importances_'):
            self.feature_importances_ = self.classifier_.feature_importances_
        if hasattr(self.classifier_, 'coef_'):
            self.coef_ = self.classifier_.coef_
        if hasattr(self.classifier_, 'n_outputs_'):
            self.n_outputs_ = self.classifier_.n_outputs_
        if hasattr(self.classifier_, 'n_features_'):
            self.n_features_ = self.classifier_.n_features_

        return self

    def predict(self, X):
        """
        Predict class probabilities of X.
        """
        check_is_fitted(self, 'classifier_')
        proba = self.classifier_.predict_proba(X)
        return proba[:, 1]

    def predict_label(self, X):
        """Predict class label of X
        """
        check_is_fitted(self, 'classifier_')
        return self.classifier_.predict(X)


class _BinarizeTargetScorer(_BaseScorer):
    """
    base class to make binarized target specific scorer
    """
    def __call__(self, clf, X, y, sample_weight=None):
        # support pipeline object
        if isinstance(clf, Pipeline):
            clf = clf.steps[-1][-1]
        if clf.less_is_positive:
            y_trans = y < clf.discretize_value
        else:
            y_trans = y > clf.discretize_value
        y_pred = clf.predict(X)
        if sample_weight is not None:
            return self._sign * self._score_func(y_trans, y_pred,
                                                 sample_weight=sample_weight,
                                                 **self._kwargs)
        else:
            return self._sign * self._score_func(y_trans, y_pred, **self._kwargs)


class _BinarizeTargetProbaScorer(_BaseScorer):
    """
    base class to make binarized target specific scorer
    """
    def __call__(self, clf, X, y, sample_weight=None):
        # support pipeline object
        if isinstance(clf, Pipeline):
            clf = clf.steps[-1][-1]
        if clf.less_is_positive:
            y_trans = y < clf.discretize_value
        else:
            y_trans = y > clf.discretize_value
        y_pred = clf.predict(X)
        if sample_weight is not None:
            return self._sign * self._score_func(y_trans, y_pred,
                                                 sample_weight=sample_weight,
                                                 **self._kwargs)
        else:
            return self._sign * self._score_func(y_trans, y_pred, **self._kwargs)


#accuracy
binarize_accuracy_scorer = _BinarizeTargetScorer(metrics.accuracy_score, 1, {})

#balanced_accuracy
binarize_balanced_accuracy_scorer = _BinarizeTargetScorer(metrics.balanced_accuracy_score, 1, {})
 
#precision
binarize_precision_scorer = _BinarizeTargetScorer(metrics.precision_score, 1, {})

#recall
binarize_recall_scorer = _BinarizeTargetScorer(metrics.recall_score, 1, {})

#roc_auc
binarize_auc_scorer = _BinarizeTargetProbaScorer(metrics.roc_auc_score, 1, {})

# average_precision_scorer
binarize_average_precision_scorer = _BinarizeTargetProbaScorer(metrics.average_precision_score, 1, {})

# roc_auc_scorer
iraps_auc_scorer = binarize_auc_scorer

# average_precision_scorer
iraps_average_precision_scorer = binarize_average_precision_scorer


class BinarizeTargetRegressor(BaseEstimator, RegressorMixin):
    """
    Extend regression estimator to have discretize_value

    Parameters
    ----------
    regressor: object
        Estimator object such as derived from sklearn `RegressionMixin`.

    z_score: float, default=-1.0
        Threshold value based on z_score. Will be ignored when
        fixed_value is set

    value: float, default=None
        Threshold value

    less_is_positive: boolean, default=True
        When target is less the threshold value, it will be converted
        to True, False otherwise.

    Attributes
    ----------
    regressor_: object
        Fitted regressor

    discretize_value: float
        The threshold value used to discretize True and False targets
    """
    def __init__(self, regressor, z_score=-1, value=None, less_is_positive=True):
        self.regressor = regressor
        self.z_score = z_score
        self.value = value
        self.less_is_positive = less_is_positive

    def fit(self, X, y, sample_weight=None):
        """
        Calculate the discretize_value fit the regressor with traning data

        Returns
        ------
        self: object
        """
        y = check_array(y, accept_sparse=False, force_all_finite=True,
                        ensure_2d=False, dtype='numeric')
        y = column_or_1d(y)

        if self.value is None:
            discretize_value = y.mean() + y.std() * self.z_score
        else:
            discretize_value = self.Value
        self.discretize_value = discretize_value

        self.regressor_ = clone(self.regressor)

        if sample_weight is not None:
            self.regressor_.fit(X, y, sample_weight=sample_weight)
        else:
            self.regressor_.fit(X, y)

        # attach classifier attributes
        if hasattr(self.regressor_, 'feature_importances_'):
            self.feature_importances_ = self.regressor_.feature_importances_
        if hasattr(self.regressor_, 'coef_'):
            self.coef_ = self.regressor_.coef_
        if hasattr(self.regressor_, 'n_outputs_'):
            self.n_outputs_ = self.regressor_.n_outputs_
        if hasattr(self.regressor_, 'n_features_'):
            self.n_features_ = self.regressor_.n_features_

        return self

    def predict(self, X):
        """Predict target value of X
        """
        check_is_fitted(self, 'regressor_')
        y_pred = self.regressor_.predict(X)
        if not np.all((y_pred>=0) & (y_pred<=1)):
            y_pred = (y_pred - y_pred.min()) / (y_pred.max() - y_pred.min())
        y_pred = 1 - y_pred
        return y_pred


# roc_auc_scorer
regression_auc_scorer = binarize_auc_scorer

# average_precision_scorer
regression_average_precision_scorer = binarize_average_precision_scorer
