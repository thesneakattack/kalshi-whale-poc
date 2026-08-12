Source: https://docs.kalshi.com/api-reference/search/get-tags-for-series-categories.md

# Get Tags for Series Categories

Retrieve tags organized by series categories.

The fetched page says this returns a mapping of series categories to associated tags for filtering and search.

## Route

`GET /search/tags_by_categories`

## Response Shape

```yaml
GetTagsForSeriesCategoriesResponse:
  tags_by_categories:
    <category>: [string]
```

This file is a local copy of the fetched page content used during this session.
