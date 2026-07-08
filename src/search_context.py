"""CLI：按 query 搜索记忆并打印上下文（调试用）"""

import sys

from hybrid_search import detect_project, format_results_lines, hybrid_search


def main() -> None:
    query = sys.argv[1] if len(sys.argv) > 1 else ''
    if not query.strip():
        print('（空查询，跳过记忆搜索）')
        return

    project = detect_project()
    results = hybrid_search(query, project=project, max_results=8)
    if not results:
        return

    print(format_results_lines(results, header='[local-memory搜索结果]'))


if __name__ == '__main__':
    main()
