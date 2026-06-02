"""
Load Knowledge - Loads table metadata, queries, and business rules into knowledge base.

Usage:
    python scripts/load_knowledge.py             # Upsert (update existing)
    python scripts/load_knowledge.py --recreate  # Drop and reload all
"""

from dash.paths import KNOWLEDGE_DIR


def load_knowledge(recreate: bool = False) -> None:
    """Load knowledge files into the vector database.

    Args:
        recreate: Drop existing knowledge and reload from scratch.
    """
    from dash.settings import dash_knowledge

    if recreate:
        print("Recreating knowledge base (dropping existing data)...\n")
        if dash_knowledge.vector_db:
            dash_knowledge.vector_db.drop()
            dash_knowledge.vector_db.create()

    print(f"Loading knowledge from: {KNOWLEDGE_DIR}\n")

    for subdir in ["tables", "queries", "business"]:
        path = KNOWLEDGE_DIR / subdir
        if not path.exists():
            print(f"  {subdir}/: (not found)")
            continue

        files = [f for f in path.iterdir() if f.is_file() and not f.name.startswith(".")]
        print(f"  {subdir}/: {len(files)} files")

        if files:
            dash_knowledge.insert(name=f"knowledge-{subdir}", path=str(path))

    print("\nDone!")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Load knowledge into vector database")
    parser.add_argument(
        "--recreate",
        action="store_true",
        help="Drop existing knowledge and reload from scratch",
    )
    args = parser.parse_args()
    load_knowledge(recreate=args.recreate)
