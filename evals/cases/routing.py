"""
Routing Cases
=============

Leader delegates to the right specialist and triggers the right tools.
Eval type: ReliabilityEval (expected tool calls)

In coordinate mode, the Leader calls `delegate_task_to_member` which then
triggers member-level tools. ReliabilityEval sees all tool calls in the chain.
"""

CASES: list[dict] = [
    # Analyst cases — Leader delegates, Analyst runs SQL/knowledge tools
    {
        "input": "What's our current MRR?",
        "expected_tools": ["delegate_task_to_member", "run_sql_query"],
    },
    {
        "input": "Which plan has the highest churn rate?",
        "expected_tools": ["delegate_task_to_member", "run_sql_query"],
    },
    {
        "input": "Show me the customers table schema",
        "expected_tools": ["delegate_task_to_member", "introspect_schema"],
    },
    {
        "input": "How many active subscriptions do we have?",
        "expected_tools": ["delegate_task_to_member", "run_sql_query"],
    },
    {
        "input": "What are the top cancellation reasons?",
        "expected_tools": ["delegate_task_to_member", "run_sql_query"],
    },
    # Engineer cases — Leader delegates to Engineer for schema work
    {
        "input": "Create a view for monthly MRR by plan",
        "expected_tools": ["delegate_task_to_member", "run_sql_query", "update_knowledge"],
    },
]
