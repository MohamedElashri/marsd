"""Typed evidence graph. All arrows run from prerequisite to potential dependent."""

from __future__ import annotations

import json
from collections import deque
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from pydantic import Field, ValidationError

from marsd.domain import MarsdError, StrictModel, digest, dumps, now

if TYPE_CHECKING:
    from marsd.registry import Registry

Kind = Literal["artifact_version", "run", "contract", "validation", "entity", "reference_set"]
Evidence = Literal["observed", "declared", "inferred"]
RELATIONS = {
    "used": ({"artifact_version"}, {"run"}),
    "generated": ({"run"}, {"artifact_version"}),
    "validated": ({"artifact_version"}, {"validation"}),
    "applies": ({"contract"}, {"validation"}),
    "checked_against": ({"reference_set"}, {"validation"}),
    "references": ({"entity"}, {"artifact_version"}),
    "derived": ({"artifact_version"}, {"artifact_version"}),
}


class Node(StrictModel):
    id: str = Field(min_length=1, max_length=256, pattern=r"^[^\s/\\\x00-\x1f]+$")
    kind: Kind
    label: str = Field(min_length=1, max_length=512)


class Edge(StrictModel):
    source: str = Field(min_length=1, max_length=256, pattern=r"^[^\s/\\\x00-\x1f]+$")
    target: str = Field(min_length=1, max_length=256, pattern=r"^[^\s/\\\x00-\x1f]+$")
    relation: Literal[
        "used", "generated", "validated", "applies", "checked_against", "references", "derived"
    ]
    evidence: Literal["declared", "inferred"] = "declared"


class Declaration(StrictModel):
    schema_version: Literal[1] = 1
    importer_id: str = Field(min_length=1, max_length=128)
    event_id: str = Field(min_length=1, max_length=128)
    nodes: list[Node] = Field(default_factory=list, max_length=1000)
    edges: list[Edge] = Field(default_factory=list, max_length=3000)


class Graph:
    def __init__(self, registry: Registry):
        self.registry = registry
        self.db = registry.db

    def node(self, identifier: str, kind: str, label: str, metadata: Any = None) -> None:
        row = self.db.execute("SELECT kind FROM graph_nodes WHERE id=?", (identifier,)).fetchone()
        if row and row[0] != kind:
            raise MarsdError("ENTITY_CONFLICT", "Entity ID already names another entity kind.")
        self.db.execute(
            "INSERT OR IGNORE INTO graph_nodes VALUES(?,?,?,?)",
            (identifier, kind, label[:512], dumps(metadata or {})),
        )

    def edge(self, source: str, target: str, relation: str, evidence: str, reference: str) -> None:
        kinds = {
            r["id"]: r["kind"]
            for r in self.db.execute(
                "SELECT id,kind FROM graph_nodes WHERE id IN (?,?)", (source, target)
            )
        }
        allowed = RELATIONS.get(relation)
        if (
            not allowed
            or kinds.get(source) not in allowed[0]
            or kinds.get(target) not in allowed[1]
        ):
            raise MarsdError(
                "INVALID_EDGE", "Relation endpoints are missing or have incompatible kinds."
            )
        identifier = digest([source, target, relation, evidence, reference])
        self.db.execute(
            "INSERT OR IGNORE INTO graph_edges VALUES(?,?,?,?,?,?,?)",
            (identifier, source, target, relation, evidence, reference, now()),
        )

    def validation(
        self, report_id: str, result: dict[str, Any], contract: dict[str, Any] | None
    ) -> None:
        self.node(
            report_id, "validation", result.get("outcome", "UNKNOWN"), {"report_id": report_id}
        )
        for source in result.get("inputs", []):
            self.node(source["version_id"], "artifact_version", source["name"])
            self.edge(source["version_id"], report_id, "validated", "observed", report_id)
        if contract:
            identifier = digest(contract)
            self.node(identifier, "contract", contract["name"])
            self.edge(identifier, report_id, "applies", "observed", report_id)
            for rule in contract["rules"]:
                reference = contract.get("references", {}).get(rule.get("reference"))
                if rule["kind"] == "membership" and reference:
                    rid = "reference-set:" + digest(reference)
                    self.node(
                        rid, "reference_set", f"{reference['namespace']}@{reference['version']}"
                    )
                    self.edge(rid, report_id, "checked_against", "declared", report_id)

    def import_file(self, path: str | Path) -> dict[str, Any]:
        source = self.registry.source(path)
        with source.open("rb") as stream:
            raw = stream.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise MarsdError("BUDGET_EXCEEDED", "Lineage declaration exceeds 1 MiB.")
        try:
            body = Declaration.model_validate_json(raw)
        except ValidationError as error:
            raise MarsdError(
                "INVALID_DECLARATION",
                "Lineage declaration does not match its closed schema.",
                exit_code=2,
            ) from error
        return self.import_declaration(body)

    def import_declaration(self, body: Declaration) -> dict[str, Any]:
        fingerprint = digest(body.model_dump())
        if len({node.id for node in body.nodes}) != len(body.nodes):
            raise MarsdError("INVALID_DECLARATION", "Node IDs must be unique in a declaration.")
        with self.registry.guard, self.db:
            old = self.db.execute(
                "SELECT digest FROM graph_imports WHERE importer_id=? AND event_id=?",
                (body.importer_id, body.event_id),
            ).fetchone()
            if old:
                if old[0] != fingerprint:
                    raise MarsdError(
                        "IMPORT_CONFLICT", "This importer/event ID already has different content."
                    )
                return {"imported": False, "idempotent": True, "digest": fingerprint}
            for node in body.nodes:
                existing = self.db.execute(
                    "SELECT kind,label FROM graph_nodes WHERE id=?", (node.id,)
                ).fetchone()
                if node.kind != "entity" and not existing:
                    raise MarsdError(
                        "UNKNOWN_ENTITY",
                        "Declarations may create scientific entities only; reference real internal IDs.",
                    )
                if existing and (
                    existing["kind"] != node.kind
                    or node.kind == "entity"
                    and existing["label"] != node.label
                ):
                    raise MarsdError(
                        "ENTITY_CONFLICT", "Declaration conflicts with an existing entity."
                    )
                self.node(node.id, node.kind, node.label)
            for edge in body.edges:
                self.edge(
                    edge.source,
                    edge.target,
                    edge.relation,
                    edge.evidence,
                    dumps([body.importer_id, body.event_id]),
                )
            self.db.execute(
                "INSERT INTO graph_imports VALUES(?,?,?)",
                (body.importer_id, body.event_id, fingerprint),
            )
        return {"imported": True, "idempotent": False, "digest": fingerprint}

    def query(
        self,
        identifier: str,
        *,
        direction: str = "downstream",
        depth: int = 1,
        max_nodes: int = 200,
        max_edges: int = 500,
        include_inferred: bool = False,
    ) -> dict[str, Any]:
        if (
            direction not in {"upstream", "downstream"}
            or not 0 <= depth <= 20
            or not 1 <= max_nodes <= 1000
            or not 1 <= max_edges <= 3000
        ):
            raise MarsdError(
                "INVALID_REQUEST", "Graph direction/depth/node/edge budget is invalid.", exit_code=2
            )
        with self.registry.guard:
            first = self.db.execute(
                "SELECT * FROM graph_nodes WHERE id=?", (identifier,)
            ).fetchone()
            if not first:
                raise MarsdError("NOT_FOUND", "No graph entity has this ID.")
            nodes = {identifier: dict(first)}
            edges: dict[str, dict[str, Any]] = {}
            levels = {identifier: 0}
            pending = deque([identifier])
            limited = False
            column = "source" if direction == "downstream" else "target"
            other = "target" if direction == "downstream" else "source"
            while pending:
                current = pending.popleft()
                scope = "" if include_inferred else " AND evidence!='inferred'"
                if levels[current] >= depth:
                    if self.db.execute(
                        f"SELECT 1 FROM graph_edges WHERE {column}=?{scope} LIMIT 1", (current,)
                    ).fetchone():
                        limited = True
                    continue
                rows = self.db.execute(
                    f"SELECT * FROM graph_edges WHERE {column}=?{scope} ORDER BY id LIMIT ?",
                    (current, max_edges + 1),
                )
                for row in rows:
                    edge = dict(row)
                    if edge["relation"] == "generated":
                        partial = self.db.execute(
                            "SELECT MAX(partial) FROM run_refs WHERE run_id=? AND version_id=? AND role='output'",
                            (edge["source"], edge["target"]),
                        ).fetchone()[0]
                        edge["partial_output"] = bool(partial) if partial is not None else None
                        run = self.db.execute(
                            "SELECT state FROM runs WHERE id=?", (edge["source"],)
                        ).fetchone()
                        if run and run[0] != "SUCCEEDED":
                            edge["partial_output"] = True
                    target = edge[other]
                    if edge["id"] in edges:
                        continue
                    if len(edges) >= max_edges or target not in nodes and len(nodes) >= max_nodes:
                        limited = True
                        break
                    edges[edge["id"]] = edge
                    if target not in nodes:
                        nodes[target] = dict(
                            self.db.execute(
                                "SELECT * FROM graph_nodes WHERE id=?", (target,)
                            ).fetchone()
                        )
                        levels[target] = levels[current] + 1
                        pending.append(target)
            for node in nodes.values():
                node["metadata"] = json.loads(node["metadata"])
                node["depth"] = levels[node["id"]]
                if node["kind"] == "run":
                    run = self.db.execute(
                        "SELECT state FROM runs WHERE id=?", (node["id"],)
                    ).fetchone()
                    if run:
                        node["metadata"]["state"] = run[0]
            return {
                "schema_version": 1,
                "seed": identifier,
                "direction": direction,
                "nodes": list(nodes.values()),
                "edges": list(edges.values()),
                "coverage": {
                    "complete": not limited,
                    "depth": depth,
                    "max_nodes": max_nodes,
                    "max_edges": max_edges,
                    "include_inferred": include_inferred,
                },
                "limitation": "Documented dependencies only. Missing links are unknown; downstream means potentially dependent, not scientifically invalid.",
                "continuation": "Expand a visible entity or increase explicit budgets."
                if limited
                else None,
            }
