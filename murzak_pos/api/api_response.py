def pos_response(
    *,
    status: str,
    message: str,
    code: str = None,
    lpo_no: str = None,  # Ensure lpo_no is included
    grn_no: str = None,
    data: dict = None,
    _server_messages: list = None  # Optional, only for debug messages
):
    """
    Normalized POS API response
    - Always includes `lpo_no` if provided
    """
    response = {
        "status": status,
        "code": code,
        "message": message,
        "data": data or {}
    }

    if lpo_no:
        response["lpo_no"] = lpo_no  # Include lpo_no if provided
    if grn_no:
        response["grn_no"] = grn_no  # Include grn_no if provided
    if _server_messages:
        response["_server_messages"] = _server_messages  # Include debug info if available

    return response
