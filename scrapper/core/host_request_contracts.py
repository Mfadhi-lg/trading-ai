"""Administrative host waits are distinct from HTTP/transport failures."""
class AdministrativeDefer(RuntimeError):
    def __init__(self, decision):
        super().__init__(decision.reason)
        self.reason = decision.reason
        self.retry_at = decision.retry_at
        self.steps = ()
