import h5py
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from marsd.adapters import Dataset
from marsd.domain import MarsdError, Request
from marsd.explore import compare


def test_hdf_metadata_and_explicit_slice(demo):
    with Dataset(demo / "data/run_042.h5") as data:
        metadata = data.inspect()
        node = next(n for n in metadata["nodes"] if n["path"] == "/measurement/transmission")
        assert node["shape"] == [64, 64]
        values, descriptor, coverage = data.array("/measurement/transmission", "24:25,31:32")
        assert values.shape == (1, 1)
        assert descriptor["unit"] == "dimensionless"
        assert coverage["visited"] == 1 and not coverage["complete"]
        with pytest.raises(MarsdError, match="positive-step"):
            data.array("/measurement/transmission", "::-1,:")
        with pytest.raises(MarsdError, match="budget"):
            data.array("/measurement/transmission", cap=3)


def test_hdf_value_diff_matches_independent_known_change(demo):
    with Dataset(demo / "data/run_041.h5") as a, Dataset(demo / "data/run_042.h5") as b:
        result = compare(
            a,
            b,
            Request(
                operation="diff",
                path=str(a.path),
                node="/measurement/transmission",
                selection=":,:",
            ),
        )
        assert result["outcome"] == "DIFFERENT"
        assert result["different_cells"] == 1
        assert result["examples"][0]["selection_flat_index"] == 24 * 64 + 31
        exact = compare(a, b, Request(operation="diff", path=str(a.path), node="/large_integer"))
        assert exact["different_cells"] == 1


def test_external_soft_links_and_virtual_storage_are_metadata_only(demo):
    outside = demo.parent / "outside.h5"
    with h5py.File(outside, "w") as f:
        f["secret"] = [1, 2, 3]
    path = demo / "data/links.h5"
    with h5py.File(path, "w") as f:
        f["external"] = h5py.ExternalLink(str(outside), "/secret")
        f["soft"] = h5py.SoftLink("/external")
        layout = h5py.VirtualLayout(shape=(3,), dtype="int64")
        layout[:] = h5py.VirtualSource(str(outside), "/secret", shape=(3,))
        f.create_virtual_dataset("virtual", layout)
    with Dataset(path) as data:
        nodes = data.inspect()["nodes"]
        assert {n["kind"] for n in nodes} >= {"group", "link", "array"}
        for node in ["/external", "/soft", "/virtual"]:
            with pytest.raises(MarsdError, match="metadata-only|Virtual"):
                data.array(node)


def test_compound_and_rank_three_selection(demo):
    path = demo / "data/special.h5"
    with h5py.File(path, "w") as f:
        f["compound"] = np.array([(1, 2.0)], dtype=[("a", "i4"), ("b", "f8")])
        f["cube"] = np.arange(24).reshape(2, 3, 4)
    with Dataset(path) as data:
        with pytest.raises(MarsdError, match="fixed-size"):
            data.array("/compound")
        with pytest.raises(MarsdError, match="two dimensions"):
            data.array("/cube", preview=True)
        values, _, _ = data.array("/cube", "1,:,:", preview=True)
        assert values.tolist() == [[12, 13, 14, 15], [16, 17, 18, 19], [20, 21, 22, 23]]


def test_csv_preserves_identifiers_and_marks_prefix(demo):
    with Dataset(demo / "data/identifiers.csv") as data:
        scan = data.scan(1)
        assert scan.rows[0]["sample_id"] == "0001"
        assert scan.rows[0]["value"] == "1.25"
        assert not scan.complete and scan.total is None
        assert data.scan(10).rows[1]["value"] is None


def test_keyed_diff_rejects_duplicates_and_oversized_inputs(demo):
    path = demo / "data/duplicates.parquet"
    pq.write_table(pa.table({"id": [1, 1], "v": [2.0, 3.0]}), path)
    with Dataset(path) as a, Dataset(path) as b:
        with pytest.raises(MarsdError, match="not unique"):
            compare(a, b, Request(operation="diff", path=str(path), keys=["id"]))
        with pytest.raises(MarsdError, match="row budget"):
            compare(a, b, Request(operation="diff", path=str(path), keys=["id"], max_rows=1))


def test_partial_positional_match_is_inconclusive(demo):
    path = demo / "data/run_041.parquet"
    with Dataset(path) as a, Dataset(path) as b:
        result = compare(a, b, Request(operation="diff", path=str(path), position=True, max_rows=2))
        assert result["outcome"] == "INCONCLUSIVE"
        assert not result["coverage"]["counts_complete"]


def test_table_null_nan_inf_comparison(demo):
    path = demo / "data/nonfinite.parquet"
    pq.write_table(
        pa.table({"id": [1, 2, 3, 4], "v": [None, float("nan"), float("inf"), -0.0]}), path
    )
    with Dataset(path) as a, Dataset(path) as b:
        result = compare(a, b, Request(operation="diff", path=str(path), keys=["id"]))
        assert result["different_cells"] == 1 and result["paired_nans"] == 1
        assert (
            compare(a, b, Request(operation="diff", path=str(path), keys=["id"], nan_equal=True))[
                "outcome"
            ]
            == "EQUAL"
        )


def test_large_decoded_hdf_chunk_is_refused_before_value_read(demo):
    path = demo / "data/oversized_chunk.h5"
    with h5py.File(path, "w") as f:
        f.create_dataset("array", shape=(64 * 1024 * 1024,), dtype="f8", chunks=(64 * 1024 * 1024,))
    with Dataset(path) as data:
        with pytest.raises(MarsdError, match="single HDF5 chunk"):
            data.array("/array", "0:1")


@pytest.mark.parametrize("filename", ["مرصد بيانات.csv", "space and Unicode Ω.csv", "Case.CSV"])
def test_unicode_case_and_space_paths_preserve_identity(demo, filename):
    from marsd.registry import Registry

    path = demo / "data" / filename
    path.write_text("identifier\n0001\n", encoding="utf-8")
    registry = Registry(demo)
    try:
        reference = registry.register(path)
        assert reference["name"] == filename
        with Dataset(path) as data:
            assert data.scan(2).rows[0]["identifier"] == "0001"
    finally:
        registry.close()


def test_adapter_protocol_and_bounded_node_pages(demo):
    from marsd.adapters import DatasetAdapter

    with Dataset(demo / "data/run_042.h5") as data:
        assert isinstance(data, DatasetAdapter)
        assert data.probe()["tabular"] is False
        page = data.list_nodes("/", limit=1)
        assert len(page["nodes"]) == 1 and page["next_offset"] == 1
        assert not page["coverage"]["complete"]
        next_page = data.list_nodes("/", offset=1, limit=1)
        assert len(next_page["nodes"]) == 1 and next_page["next_offset"] is None


def test_wide_parquet_metadata_is_bounded_and_field_pages_remain_available(demo):
    path = demo / "data/wide.parquet"
    pq.write_table(pa.table({f"field_{i}": [i] for i in range(1005)}), path)
    with Dataset(path) as data:
        metadata = data.inspect()
        assert len(metadata["fields"]) == 1000
        assert metadata["total_fields"] == 1005 and not metadata["coverage"]["complete"]
        page = data.list_nodes("/", offset=1000, limit=10)
        assert len(page["nodes"]) == 5


def test_explicit_comparison_projection_is_honored_and_alignment_is_unambiguous(demo):
    a_path = demo / "data/project_a.csv"
    b_path = demo / "data/project_b.csv"
    a_path.write_text("id,value,ignored\nx,1,old\n")
    b_path.write_text("id,value,ignored\nx,1,new\n")
    with Dataset(a_path) as a, Dataset(b_path) as b:
        request = Request(operation="diff", path=str(a_path), keys=["id"], columns=["value"])
        result = compare(a, b, request)
        assert result["outcome"] == "EQUAL" and result["projection"] == ["id", "value"]
        assert result["scope"] == "projected_table_values_and_schema"
        with pytest.raises(MarsdError, match="not both"):
            compare(a, b, request.model_copy(update={"position": True}))
