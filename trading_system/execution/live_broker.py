from .broker_base import BrokerBase


class LiveBroker(BrokerBase):
    def submit(self, order):
        raise RuntimeError(
            "Live execution is disabled. Implement and validate a broker adapter, persistence, reconciliation, and recovery first."
        )
