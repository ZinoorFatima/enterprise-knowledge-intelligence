"""The RAG pipeline as a LangGraph StateGraph.

    START
      └─▶ route_rewrite ──┬─▶ rewrite ──────┐
                          └─▶ skip_rewrite ─┤
                                            ▼
                                        retrieve
                                            │
                                          fuse
                                            │
                                         rerank
                                            │
                                        assemble
                                            │
                              route_after_assemble
                                   ┌────────┴────────┐
                                   ▼                 ▼
                               generate           refuse
                                   │                 │
                            route_verify             │
                            ┌──────┴──────┐          │
                            ▼             ▼          │
                         verify     skip_verify ◀─────┘
                            └──────┬──────┘
                                   ▼
                                  END

Why a graph rather than a straight async function:

  * The conditional edges ARE the product's behaviour. "No evidence cleared the
    floors, so refuse" is a routing decision, and expressing it as an edge makes
    it reviewable instead of buried in an if-statement halfway down a function.
  * The state is the audit trail the retrieval inspector renders. Nodes append
    to it; nothing is thrown away between stages.
  * The eval harness runs this exact graph. If the eval ran a reimplementation,
    its numbers would describe a system that nobody ships.
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from app.graph.nodes import (
    assemble_node,
    fuse_node,
    generate_node,
    refuse_node,
    rerank_node,
    retrieve_node,
    rewrite_node,
    route_after_assemble,
    route_rewrite,
    route_verify,
    skip_rewrite_node,
    skip_verify_node,
    verify_node,
)
from app.graph.state import (
    Filters,
    PipelineDeps,
    PipelineResult,
    PipelineState,
)


def build_pipeline(checkpointer=None):
    """Compile the graph. Pass a checkpointer for multi-turn threads."""
    g = StateGraph(PipelineState)

    g.add_node("rewrite", rewrite_node)
    g.add_node("skip_rewrite", skip_rewrite_node)
    g.add_node("retrieve", retrieve_node)
    g.add_node("fuse", fuse_node)
    g.add_node("rerank", rerank_node)
    g.add_node("assemble", assemble_node)
    g.add_node("generate", generate_node)
    g.add_node("refuse", refuse_node)
    g.add_node("verify", verify_node)
    g.add_node("skip_verify", skip_verify_node)

    g.add_conditional_edges(
        START, route_rewrite, {"rewrite": "rewrite", "skip_rewrite": "skip_rewrite"}
    )
    g.add_edge("rewrite", "retrieve")
    g.add_edge("skip_rewrite", "retrieve")
    g.add_edge("retrieve", "fuse")
    g.add_edge("fuse", "rerank")
    g.add_edge("rerank", "assemble")

    g.add_conditional_edges(
        "assemble", route_after_assemble, {"generate": "generate", "refuse": "refuse"}
    )
    g.add_conditional_edges(
        "generate", route_verify, {"verify": "verify", "skip_verify": "skip_verify"}
    )
    # A refusal is still an answer that was not verified, and must say so.
    g.add_edge("refuse", "skip_verify")
    g.add_edge("verify", END)
    g.add_edge("skip_verify", END)

    return g.compile(checkpointer=checkpointer)


_COMPILED = None


def get_pipeline():
    """Compiled once. Graph construction is pure, so this is safe to cache."""
    global _COMPILED
    if _COMPILED is None:
        _COMPILED = build_pipeline()
    return _COMPILED


async def run_pipeline(
    *,
    question: str,
    deps: PipelineDeps,
    filters: Filters | None = None,
    history: list | None = None,
    want_rewrite: bool = True,
    want_verify: bool = True,
    force_verify: bool = False,
    top_k: int | None = None,
    graph=None,
) -> PipelineResult:
    """Run one question end to end.

    The eval harness calls this same function with the same deps protocol, which
    is what makes a measured number describe the shipped system.
    """
    graph = graph or get_pipeline()
    initial: PipelineState = {
        "question": question,
        "filters": filters or Filters(),
        "history": history or [],
        "want_rewrite": want_rewrite,
        "want_verify": want_verify,
        "force_verify": force_verify,
        "stage_latency_ms": {},
        "errors": [],
    }
    if top_k is not None:
        initial["top_k"] = top_k

    final = await graph.ainvoke(initial, config={"configurable": {"deps": deps}})
    return PipelineResult.from_state(final, question)
