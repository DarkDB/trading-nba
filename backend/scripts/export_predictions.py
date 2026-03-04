#!/usr/bin/env python3
"""
Export predictions from MongoDB to NDJSON format.

MODE: READ-ONLY. Does NOT modify any data.

Usage:
    python export_predictions.py --out /path/to/output.ndjson
    python export_predictions.py --out /tmp/predictions.ndjson --settled-only true --limit 100
    python export_predictions.py --out /tmp/predictions.ndjson --date-from 2026-02-01
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from typing import Optional

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
from pymongo import MongoClient


def parse_args():
    parser = argparse.ArgumentParser(
        description="Export predictions from MongoDB to NDJSON (READ-ONLY)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python export_predictions.py --out /tmp/predictions_settled.ndjson
  python export_predictions.py --out /tmp/predictions.ndjson --settled-only false
  python export_predictions.py --out /tmp/predictions.ndjson --limit 50
  python export_predictions.py --out /tmp/predictions.ndjson --date-from 2026-02-01
  python export_predictions.py --out /tmp/predictions.ndjson --fields id,home_team,away_team,result,profit_units
        """
    )
    parser.add_argument(
        "--out", 
        required=True, 
        help="Output file path (NDJSON format)"
    )
    parser.add_argument(
        "--settled-only", 
        type=str, 
        default="true",
        choices=["true", "false"],
        help="Export only settled predictions (default: true)"
    )
    parser.add_argument(
        "--limit", 
        type=int, 
        default=None,
        help="Limit number of documents to export (optional)"
    )
    parser.add_argument(
        "--date-from", 
        type=str, 
        default=None,
        help="Filter by settled_at >= YYYY-MM-DD (optional)"
    )
    parser.add_argument(
        "--date-to", 
        type=str, 
        default=None,
        help="Filter by settled_at <= YYYY-MM-DD (optional)"
    )
    parser.add_argument(
        "--fields", 
        type=str, 
        default=None,
        help="Comma-separated list of fields to export (optional, default: all)"
    )
    return parser.parse_args()


def build_query(args) -> dict:
    """Build MongoDB query based on CLI arguments."""
    query = {}
    
    # Settled filter
    if args.settled_only == "true":
        query["result"] = {"$in": ["WIN", "LOSS", "PUSH"]}
    
    # Date filters
    if args.date_from or args.date_to:
        date_filter = {}
        if args.date_from:
            date_filter["$gte"] = args.date_from
        if args.date_to:
            date_filter["$lte"] = args.date_to + "T23:59:59"
        
        # Try settled_at first, fallback to created_at
        query["$or"] = [
            {"settled_at": date_filter},
            {"created_at": date_filter}
        ]
    
    return query


def build_projection(args) -> Optional[dict]:
    """Build MongoDB projection based on --fields argument."""
    if not args.fields:
        return {"_id": 0}  # Exclude _id by default
    
    fields = [f.strip() for f in args.fields.split(",")]
    projection = {"_id": 0}
    for field in fields:
        projection[field] = 1
    return projection


def serialize_document(doc: dict) -> str:
    """Serialize a MongoDB document to JSON string, handling special types."""
    def default_serializer(obj):
        if isinstance(obj, datetime):
            return obj.isoformat()
        if hasattr(obj, '__str__'):
            return str(obj)
        raise TypeError(f"Object of type {type(obj)} is not JSON serializable")
    
    return json.dumps(doc, default=default_serializer, ensure_ascii=False)


def main():
    args = parse_args()
    
    # Load environment
    load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), '.env'))
    
    mongo_url = os.environ.get('MONGO_URL')
    db_name = os.environ.get('DB_NAME')
    
    if not mongo_url or not db_name:
        print("ERROR: MONGO_URL and DB_NAME must be set in environment")
        sys.exit(1)
    
    print("=" * 60)
    print("  EXPORT PREDICTIONS - READ-ONLY MODE")
    print("=" * 60)
    print(f"MongoDB URL: {mongo_url[:30]}...")
    print(f"Database: {db_name}")
    print(f"Output: {args.out}")
    print(f"Settled only: {args.settled_only}")
    print(f"Limit: {args.limit or 'None'}")
    print(f"Date from: {args.date_from or 'None'}")
    print(f"Date to: {args.date_to or 'None'}")
    print(f"Fields: {args.fields or 'All'}")
    print()
    
    # Connect to MongoDB (READ-ONLY operations)
    client = MongoClient(mongo_url)
    db = client[db_name]
    collection = db.predictions
    
    # Build query and projection
    query = build_query(args)
    projection = build_projection(args)
    
    print(f"Query: {json.dumps(query, indent=2)}")
    print()
    
    # Get cursor
    cursor = collection.find(query, projection)
    if args.limit:
        cursor = cursor.limit(args.limit)
    
    # Statistics
    stats = {
        "total_exported": 0,
        "by_result": {"WIN": 0, "LOSS": 0, "PUSH": 0, "None": 0},
        "missing_open_price": 0,
        "missing_open_spread": 0,
        "missing_clv_spread": 0,
        "missing_p_cover": 0,
        "missing_ev": 0,
        "first_settled_at": None,
        "last_settled_at": None,
        "tiers": {"A": 0, "B": 0, "C": 0, "None": 0}
    }
    
    # Export to NDJSON
    print(f"Exporting to {args.out}...")
    
    with open(args.out, 'w', encoding='utf-8') as f:
        for doc in cursor:
            # Write document as JSON line
            f.write(serialize_document(doc) + '\n')
            
            # Update statistics
            stats["total_exported"] += 1
            
            # Result counts
            result = doc.get("result")
            if result in stats["by_result"]:
                stats["by_result"][result] += 1
            else:
                stats["by_result"]["None"] += 1
            
            # Tier counts
            tier = doc.get("tier")
            if tier in stats["tiers"]:
                stats["tiers"][tier] += 1
            else:
                stats["tiers"]["None"] += 1
            
            # Missing fields
            if doc.get("open_price") is None:
                stats["missing_open_price"] += 1
            if doc.get("open_spread") is None:
                stats["missing_open_spread"] += 1
            if doc.get("clv_spread") is None:
                stats["missing_clv_spread"] += 1
            if doc.get("p_cover") is None:
                stats["missing_p_cover"] += 1
            if doc.get("ev") is None:
                stats["missing_ev"] += 1
            
            # Track settled_at range
            settled_at = doc.get("settled_at")
            if settled_at:
                if stats["first_settled_at"] is None or settled_at < stats["first_settled_at"]:
                    stats["first_settled_at"] = settled_at
                if stats["last_settled_at"] is None or settled_at > stats["last_settled_at"]:
                    stats["last_settled_at"] = settled_at
    
    # Get total settled in DB for reference
    total_settled_in_db = collection.count_documents({"result": {"$in": ["WIN", "LOSS", "PUSH"]}})
    
    # Print summary
    print()
    print("=" * 60)
    print("  EXPORT SUMMARY")
    print("=" * 60)
    print(f"Total exported:           {stats['total_exported']}")
    print(f"Total settled in DB:      {total_settled_in_db}")
    print()
    print("BY RESULT:")
    print(f"  WIN:                    {stats['by_result']['WIN']}")
    print(f"  LOSS:                   {stats['by_result']['LOSS']}")
    print(f"  PUSH:                   {stats['by_result']['PUSH']}")
    print(f"  None/Other:             {stats['by_result']['None']}")
    print()
    print("BY TIER:")
    print(f"  Tier A:                 {stats['tiers']['A']}")
    print(f"  Tier B:                 {stats['tiers']['B']}")
    print(f"  Tier C:                 {stats['tiers']['C']}")
    print(f"  None/Other:             {stats['tiers']['None']}")
    print()
    print("MISSING FIELDS:")
    print(f"  missing_open_price:     {stats['missing_open_price']}")
    print(f"  missing_open_spread:    {stats['missing_open_spread']}")
    print(f"  missing_clv_spread:     {stats['missing_clv_spread']}")
    print(f"  missing_p_cover:        {stats['missing_p_cover']}")
    print(f"  missing_ev:             {stats['missing_ev']}")
    print()
    print("DATE RANGE:")
    print(f"  First settled_at:       {stats['first_settled_at'] or 'N/A'}")
    print(f"  Last settled_at:        {stats['last_settled_at'] or 'N/A'}")
    print()
    print(f"Output file: {args.out}")
    print(f"File size: {os.path.getsize(args.out) / 1024:.2f} KB")
    print()
    print("Done. NO DATA WAS MODIFIED.")
    
    client.close()


if __name__ == "__main__":
    main()
