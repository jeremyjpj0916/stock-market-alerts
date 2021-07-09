class Stock:
    def __init__(self, stock_ticker, alerted_already):
        self.stock_ticker = stock_ticker
        self.alerted_already = alerted_already

    def __repr__(self):
        return str(self.stock_ticker) + " " + str(self.alerted_already) + "\n"
