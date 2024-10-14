class ApplicationError(Exception):
    status_code = 200
    error_code = "A0000"

    def __init__(self, payload=None, error_code=None, status_code=None):
        super().__init__()
        self.payload = payload or {}
        if error_code is not None:
            self.error_code = error_code
        if status_code is not None:
            self.status_code = status_code

    def to_dict(self):
        return dict(self.payload)
