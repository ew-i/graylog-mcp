"""Static guide served as the `graylog://guide/query-syntax` resource."""

QUERY_SYNTAX = """\
# Graylog query syntax and how this server uses it

## Query syntax (Lucene-style)

| Goal                        | Query                                            |
|-----------------------------|--------------------------------------------------|
| Term in the message text    | `ssh`                                            |
| Either term (default is OR) | `ssh login`                                      |
| Exact phrase                | `"ssh login"`                                    |
| Field value                 | `source:api-01`                                  |
| Field phrase                | `message:"connection refused"`                   |
| Several values of a field   | `level:(3 OR 4)`                                 |
| Both / neither              | `level:3 AND source:api-01`, `NOT level:6`       |
| Field is present / missing  | `_exists_:user_id`, `NOT _exists_:user_id`       |
| Wildcards                   | `source:api-*`, `pod:web-?` (not leading)        |
| Inclusive range             | `http_response_code:[500 TO 599]`                |
| Exclusive range             | `duration_ms:{0 TO 100}`                         |
| Comparison                  | `duration_ms:>1000`, `level:<=4`                 |
| Fuzzy / proximity           | `timout~`, `"disk full"~3`                       |

- `AND`, `OR` and `NOT` must be uppercase; group with parentheses.
- Field names are case-sensitive. Find them with the
  `graylog://streams/{stream_id}/fields` resource or `discover_fields`.
- The `message` field is tokenised, so whole words match; non-analysed fields
  (most structured fields) need the exact value.
- Escape these characters with a backslash when they are part of a value:
  `& | : \\ / + - ! ( ) { } [ ] ^ " ~ * ?`, e.g. `path:\\/api\\/v1`.
- Leading wildcards (`*error`) are disabled on most Graylog servers.
- Group and count by structured fields. The full-text `message` field cannot be
  used by `exact_field_counts`, `compare_windows` or `field_stats` (Graylog
  rejects it); `rank_field_values` can rank message texts from a sample.

## Using this server

1. Every search and analysis needs a `stream_id`: read `graylog://streams` or
   call `browse_streams`. Access is enforced per stream.
2. Search with `find_recent_logs` (last N seconds) or `find_logs_between`
   (ISO-8601 UTC timestamps such as `2026-03-15T10:00:00.000Z`).
3. Each message carries `_index` and `_id`; pass them to `read_log_message`
   for the full message or `message_context` for its neighbours.
4. Prefer counting over reading: `count_matches`, `count_over_time`,
   `exact_field_counts` and `compare_windows`. `count_over_time`,
   `exact_field_counts`, `field_stats` and the per-value part of
   `compare_windows` need Graylog 5.1+; older servers return
   `{"kind": "Unsupported"}`.
5. Paged results return `next_cursor` while `has_more` is true; pass it back
   unchanged. `truncated` means paging alone will not return everything.
6. Failures come back as `{"error": ..., "kind": ...}` instead of raising;
   `InvalidRequest` means the arguments need fixing.
"""
