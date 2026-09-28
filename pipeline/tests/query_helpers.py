"""Arrow streaming behavior for the existing mocked Lance query chains."""
import pyarrow as pa


def add_scalar_batches(query):
    def batches(*, batch_size=None, **_kwargs):
        table = pa.Table.from_pylist(query.to_list.return_value)
        return pa.RecordBatchReader.from_batches(
            table.schema, table.to_batches(max_chunksize=batch_size),
        )
    query.to_batches.side_effect = batches
    return query
