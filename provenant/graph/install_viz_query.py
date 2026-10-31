"""Install the wallet_neighbourhood query used by the dashboard's graph picture.
Run once:  python -m provenant.graph.install_viz_query
"""
from pathlib import Path

from dotenv import load_dotenv

from .tg_graph import TigerGraphBackend, connect

if __name__ == "__main__":
    load_dotenv(".env")
    conn = connect()
    print(conn.gsql((Path(__file__).resolve().parent / "viz_queries.gsql").read_text()))
    n = TigerGraphBackend().neighbourhood("0xe0110000000000000000000000000000000000e1", 2)
    print(f"mule neighbourhood: {len(n['nodes'])} wallets, {len(n['edges'])} links, "
          f"{sum(n['nodes'].values())} flagged")
