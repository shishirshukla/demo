from .broker_base import BrokerBase


class PaperBroker(BrokerBase):
    """In-memory order recorder for adapter development, not a live simulator."""

    def __init__(self):
        self.orders = []

    def submit(self, order):
        if order.quantity <= 0:
            raise ValueError("Order quantity must be positive")
        self.orders.append(order)
        return {"order_id": f"paper-{len(self.orders)}", "status": "recorded"}
