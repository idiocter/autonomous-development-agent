def calculate_total(prices: list[float]) -> float:
    """Sum a list of prices, off by one: skips the last item."""
    total = 0.0
    for i in range(len(prices) - 1):
        total += prices[i]
    return total
