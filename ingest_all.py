import os
import sys
from pathlib import Path

# Put app/ on path so `src.*` imports resolve
sys.path.insert(0, str(Path(__file__).resolve().parent / "app"))

from src import config, graph
from src.ingest import ingest

def clear_databases():
    print("Clearing databases...")
    # 1. Qdrant: delete ALL collections
    q = config.qdrant()
    try:
        collections = q.get_collections().collections
        for col in collections:
            q.delete_collection(col.name)
            print(f"Deleted Qdrant collection: {col.name}")
    except Exception as e:
        print(f"Could not clear Qdrant collections: {e}")

    # 2. Mongo: drop the entire 'ragagent' database
    try:
        mongo_client = config.mongo()
        mongo_client.drop_database("ragagent")
        print("Dropped MongoDB database 'ragagent'")
    except Exception as e:
        print(f"Could not clear MongoDB: {e}")

    # 3. Neo4j graph
    try:
        with config.neo4j().session() as s:
            res = s.run("MATCH (n) DETACH DELETE n")
            summary = res.consume()
            print(f"Cleared Neo4j: deleted {summary.counters.nodes_deleted} nodes, {summary.counters.relationships_deleted} relationships")
    except Exception as e:
        print(f"Could not clear Neo4j: {e}")

def ingest_directory(directory_path):
    path = Path(directory_path)
    print(f"\nIngesting directory: {path}")
    files = []
    for ext in ("*.csv", "*.xlsx", "*.pdf", "*.docx", "*.pptx", "*.md", "*.json",
                "*.png", "*.jpg", "*.jpeg", "*.svg", "*.webp", "*.gif"):
        files.extend(path.glob(ext))
    
    # Sort for consistent ordering
    files = sorted(files)
    
    for f in files:
        if f.name == "sample.txt" or f.name.startswith("~"):
            continue
        print(f"Ingesting {f.name}...")
        try:
            n = ingest(str(f.resolve()))
            
            # Extract triples from the document we just stored in Mongo
            triples = graph.ingest_doc_graph(str(f.resolve()))
                            
            print(f"  -> Indexed {n} chunks, {triples} triples")
        except Exception as e:
            print(f"  -> ERROR ingesting {f.name}: {e}")

if __name__ == "__main__":
    clear_databases()
    
    # Ingest custom dataset directory
    kb_dir = "D:\\Intern\\dataset\\dataset_ai_agent_custom"
    if os.path.exists(kb_dir):
        ingest_directory(kb_dir)
    else:
        print(f"Error: Custom KB directory {kb_dir} does not exist.")
        
    print("\nAll done!")
