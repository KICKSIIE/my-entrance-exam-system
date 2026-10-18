"""
Real face recognition logic using the face_recognition library.

These functions work on raw image bytes (decoded from the base64
photo sent by the kiosk's camera capture). Kept independent of Flask
so they can be tested directly with sample images later, if needed.
"""
import base64
import io
import numpy as np
from PIL import Image
import face_recognition


def _decode_image(image_data):
    """
    The kiosk sends the photo as a base64 data URL, like:
    'data:image/jpeg;base64,/9j/4AAQSkZJRg...'
    This strips the header and turns it into a NumPy array
    (the format face_recognition expects).
    """
    header, encoded = image_data.split(',', 1)
    image_bytes = base64.b64decode(encoded)
    image = Image.open(io.BytesIO(image_bytes)).convert('RGB')
    return np.array(image)


def check_face_quality(image_data):
    """
    Checks that exactly one face is visible and detectable.
    Returns: (is_valid: bool, reason: str)
    """
    image_array = _decode_image(image_data)
    face_locations = face_recognition.face_locations(image_array)

    if len(face_locations) == 0:
        return False, "No face detected. Please make sure your face is clearly visible."
    if len(face_locations) > 1:
        return False, "Multiple faces detected. Please make sure only you are in frame."

    return True, "OK"


def encode_face(image_data):
    """
    Converts the photo into a 128-number face encoding.
    Returns: list[float] if a face was found, otherwise None.
    """
    image_array = _decode_image(image_data)
    encodings = face_recognition.face_encodings(image_array)

    if len(encodings) == 0:
        return None

    # face_recognition returns a NumPy array - convert to a plain
    # Python list so it can be stored as JSON in the database.
    return encodings[0].tolist()

def _best_match(new_encoding, all_applicants):
    """Returns (closest_applicant, its_distance, second_closest_distance)."""
    candidates = [a for a in all_applicants if a.face_encoding]
    if not candidates:
        return None, None, None

    stored = np.array([a.face_encoding for a in candidates])
    distances = face_recognition.face_distance(stored, np.array(new_encoding))
    order = np.argsort(distances)

    best = order[0]
    second = float(distances[order[1]]) if len(order) > 1 else None
    print(f"[face] closest={candidates[best].registration_number} "
          f"distance={distances[best]:.3f} second={second}")  # remove once tuned
    return candidates[best], float(distances[best]), second

def find_duplicate(new_encoding, all_applicants, threshold=0.5):
    """Registration: is this face already registered?"""
    applicant, dist, _ = _best_match(new_encoding, all_applicants)
    if applicant is not None and dist <= threshold:
        return applicant
    return None

def find_match(new_encoding, all_applicants, threshold=0.5, margin=0.05):
    """Time In: identify who this is. Must be close AND clearly closer
    than the runner-up, otherwise treat as 'not identified'."""
    applicant, dist, second = _best_match(new_encoding, all_applicants)
    if applicant is None or dist > threshold:
        return None
    if second is not None and (second - dist) < margin:
        return None
    return applicant

def verify_identity(applicant, live_image_data, threshold=0.6):
    """
    Confirms the live photo matches the applicant's stored encoding.
    Returns: True if it's a match, False otherwise.
    """
    if not applicant.face_encoding:
        return False

    live_encoding = encode_face(live_image_data)
    if live_encoding is None:
        return False

    stored_encoding_array = np.array(applicant.face_encoding)
    live_encoding_array = np.array(live_encoding)

    distance = face_recognition.face_distance([stored_encoding_array], live_encoding_array)[0]
    return distance <= threshold