class DuplicateUploadError(RuntimeError):
    """Garmin rejected an upload because the activity already exists."""


class UploadConsentRequiredError(RuntimeError):
    """Garmin requires the account owner to grant data upload consent."""
