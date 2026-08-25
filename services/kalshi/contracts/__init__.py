"""Channel-/operation-specific Kalshi contract normalizers (Phase A Tasks
A10/A12). One focused module per documented message family - trade, ticker,
fill, position, lifecycle, order - each carrying its own CONTRACT_DOCS
mapping back to the exact docs/kalshi/ pages it implements. Empty at A4:
the skeleton exists so the boundary (and its CI scanner) covers the
contracts namespace from the start; the first real normalizer lands with
A10.
"""
