import argparse
from similar_sounds import find_similar_sounds


def main() -> int:
    parser = argparse.ArgumentParser(description="Query Ableton Live 12's indexed sound-similarity feature vectors.")
    parser.add_argument("base", help="Base file_id or a LIKE-able filename fragment, e.g. 'Kick 18 Inch'")
    parser.add_argument("--db", dest="db_path", help="Live-files-*.db path")
    parser.add_argument("--limit", type=int, default=12)
    parser.add_argument("--include-self", action="store_true")
    args = parser.parse_args()

    result = find_similar_sounds({
        "base": args.base,
        "db_path": args.db_path,
        "limit": args.limit,
        "include_self": args.include_self,
    })
    base = result["base"]
    print("database: %s" % result["database"])
    print("base: %(file_id)s %(name)s (file_kind=%(file_kind)s, fe_version=%(fe_version)s)" % base)
    print()
    for item in result["results"]:
        print("  %8.5f  %7s  %s  [%s]" % (item["distance"], item["file_id"], item["name"], item["place"]))
        print("    %s" % item["path"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
