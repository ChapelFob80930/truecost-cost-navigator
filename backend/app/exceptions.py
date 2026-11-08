"""Domain exceptions. The API layer maps these to HTTP status codes in one place (main.py)."""


class DomainError(Exception):
    status_code = 400


class UnknownCodeError(DomainError):
    status_code = 404

    def __init__(self, codes):
        self.codes = list(codes)
        super().__init__(f"Unknown or unsupported code(s): {', '.join(self.codes)}")


class InvalidInsuranceError(DomainError):
    status_code = 422


class InvalidPlanError(DomainError):
    status_code = 422

    def __init__(self, insurer, plan, available):
        super().__init__(f"No plan '{plan}' found for {insurer}. Available plan types: {', '.join(available) or 'none'}.")


class UnknownHospitalError(DomainError):
    status_code = 404

    def __init__(self, name, available):
        super().__init__(f"Unknown hospital '{name}'. Choose one of: {', '.join(available)}.")


class NoBillableCodesError(DomainError):
    status_code = 422

    def __init__(self):
        super().__init__("Only diagnosis (ICD-10) codes were given; add at least one procedure (CPT) code to price.")


class InvalidStatsQuery(DomainError):
    status_code = 422

    def __init__(self, buckets):
        super().__init__(f"bucket_minutes must be one of {', '.join(map(str, buckets))}, and the window can hold at most 300 buckets.")
