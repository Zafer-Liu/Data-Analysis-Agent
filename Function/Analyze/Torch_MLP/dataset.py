"""Compatibility names for the one versioned, verified streaming training adapter."""
from Function.Analyze.Torch_MLP.kernel import BatchSource, partition_rows
from remote_runner.protocol import validate_budget


class BatchDataset(BatchSource):
    def __init__(self, source, *, budget=None, batch_rows=256, work_dir=None):
        super().__init__(source, validate_budget(budget), batch_rows, work_dir)

    def materialize(self, root=None):
        return self.parquet_files()


def partition(indices, seed, validation_split):
    return partition_rows(indices, seed, validation_split)


def labels(series):
    return series.astype("string").fillna("<missing>").astype(str)
