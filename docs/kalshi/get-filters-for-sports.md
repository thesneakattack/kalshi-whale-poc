Source: https://docs.kalshi.com/api-reference/search/get-filters-for-sports.md

# Get Filters for Sports

Retrieve available filters organized by sport.

The fetched page says this returns filtering options for each sport, including scopes and competitions, plus an ordered list of sports for display.

## Route

`GET /search/filters_by_sport`

## Response Shape

```yaml
GetFiltersBySportsResponse:
  filters_by_sports:
    <sport>:
      scopes: [string]
      competitions:
        <competition>:
          scopes: [string]
  sport_ordering: [string]
```

Fields surfaced in the fetch:
- `filters_by_sports`
- `sport_ordering`
- `SportFilterDetails.scopes`
- `SportFilterDetails.competitions`
- `ScopeList.scopes`

This file is a local copy of the fetched page content used during this session.
