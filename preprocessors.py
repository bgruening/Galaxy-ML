"""
Z_RandomOverSampler
"""

import imblearn
from imblearn.over_sampling.base import BaseOverSampler
from imblearn.over_sampling import RandomOverSampler
from imblearn.pipeline import Pipeline as imbPipeline
from imblearn.utils import check_target_type
from sklearn.utils import safe_indexing


class Z_RandomOverSampler(BaseOverSampler):

    def __init__(self, sampling_strategy='auto',
                 return_indices=False,
                 random_state=None,
                 ratio=None,
                 negative_thres=0,
                 positive_thres=-1):
        super(Z_RandomOverSampler, self).__init__(
            sampling_strategy=sampling_strategy, ratio=ratio)
        self.random_state = random_state
        self.return_indices = return_indices
        self.negative_thres = negative_thres
        self.positive_thres = positive_thres

    @staticmethod
    def _check_X_y(X, y):
        y, binarize_y = check_target_type(y, indicate_one_vs_all=True)
        X, y = check_X_y(X, y, accept_sparse=['csr', 'csc'], dtype=None)
        return X, y, binarize_y

    def _fit_resample(self, X, y):
        n_samples = X.shape[0]

        # convert y to z_score
        y_z = (y - y.mean()) / y.std()

        index0 = np.arange(n_samples)
        index_negative = index0[y_z > self.negative_thres]
        index_positive = index0[y_z <= self.positive_thres]
        index_unclassified = [x for x in index0 if x not in index_negative and x not in index_positive]

        y_z[index_negative] = 0
        y_z[index_positive] = 1
        y_z[index_unclassified] = -1

        ros = RandomOverSampler(sampling_strategy=self.sampling_strategy,
                                random_state=self.random_state,
                                ratio=self.ratio)
        _, _ = ros.fit_resample(X, y_z)
        sample_indices = ros.sample_indices_
        print("Before sampler: %s. Total after: %s" %(Counter(y_z), sample_indices.shape))

        self.sample_indices_ = np.array(sample_indices)

        if self.return_indices:
            return (safe_indexing(X, sample_indices),
                    safe_indexing(y, sample_indices), sample_indices)
        return (safe_indexing(X, sample_indices),
                safe_indexing(y, sample_indices))
