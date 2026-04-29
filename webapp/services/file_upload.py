"""
File upload handling for the DrugCLIP web application.

Provides the FileUploadHandler class for validating, saving, and managing
user-uploaded files in session-isolated directories.
"""

import os
import shutil

from werkzeug.utils import secure_filename

from webapp.config import MAX_FILE_SIZE, UPLOAD_FOLDER
from webapp.services.validation import validate_file_extension


class ValidationError(Exception):
    """Raised when a file upload fails validation (extension or size)."""

    pass


class FileUploadHandler:
    """Handles file upload validation, storage, and cleanup.

    Files are stored under ``webapp/uploads/<session_id>/`` to ensure
    session isolation.
    """

    def get_upload_dir(self, session_id: str) -> str:
        """Return the upload directory for a session, creating it if needed.

        Parameters
        ----------
        session_id:
            The unique session identifier.

        Returns
        -------
        str
            Absolute path to the session's upload directory.
        """
        upload_dir = os.path.join(UPLOAD_FOLDER, session_id)
        os.makedirs(upload_dir, exist_ok=True)
        return upload_dir

    def validate_and_save(self, file, session_id: str, file_type: str) -> str:
        """Validate a file upload and save it to the session directory.

        Checks that the file has an allowed extension for the given
        *file_type* and that its size does not exceed the configured maximum.
        On success the file is saved to ``webapp/uploads/<session_id>/<filename>``
        using a sanitized filename.

        Parameters
        ----------
        file:
            A Werkzeug ``FileStorage`` object from a Flask file upload.
        session_id:
            The unique session identifier.
        file_type:
            One of ``'pdb'``, ``'library'``, or ``'ligand'``.

        Returns
        -------
        str
            The absolute path where the file was saved.

        Raises
        ------
        ValidationError
            If the file has no filename, an unsupported extension, or exceeds
            the maximum allowed size.
        """
        # Ensure a filename is present
        if not file or not file.filename:
            raise ValidationError("No file was provided.")

        filename = secure_filename(file.filename)
        if not filename:
            raise ValidationError("Invalid filename.")

        # Validate extension
        if not validate_file_extension(filename, file_type):
            from webapp.config import ALLOWED_EXTENSIONS

            allowed = ALLOWED_EXTENSIONS.get(file_type, set())
            extensions_str = ", ".join(sorted(allowed))
            raise ValidationError(
                f"Unsupported file extension. Accepted formats: {extensions_str}"
            )

        # Validate file size
        # content_length may be 0 or None for chunked uploads, so we also
        # check after saving if needed. But if content_length is set and
        # exceeds the limit, reject early.
        if file.content_length and file.content_length > MAX_FILE_SIZE:
            raise ValidationError(
                "File exceeds the maximum allowed size of 500 MB."
            )

        # Save the file
        upload_dir = self.get_upload_dir(session_id)
        save_path = os.path.join(upload_dir, filename)
        file.save(save_path)

        # Post-save size check (handles cases where content_length was not set)
        if os.path.getsize(save_path) > MAX_FILE_SIZE:
            os.remove(save_path)
            raise ValidationError(
                "File exceeds the maximum allowed size of 500 MB."
            )

        return save_path

    def cleanup_session(self, session_id: str) -> None:
        """Remove all uploaded files for a session.

        Deletes the entire session upload directory and its contents.
        Does nothing if the directory does not exist.

        Parameters
        ----------
        session_id:
            The unique session identifier.
        """
        upload_dir = os.path.join(UPLOAD_FOLDER, session_id)
        if os.path.isdir(upload_dir):
            shutil.rmtree(upload_dir)
