"""
MPESA (Daraja) Constants
Contains all Daraja API constants, result codes, and endpoint definitions
"""

# ============================================================================
# Daraja Result Codes
# ============================================================================

# Success
MPESA_SUCCESS = 0

# User Actions
MPESA_USER_CANCELLED = 1032

# Insufficient Funds
MPESA_INSUFFICIENT_FUNDS = 2001
MPESA_INSUFFICIENT_BALANCE = 1

# Invalid Data
MPESA_INVALID_MSISDN = 17

# Timeout
MPESA_TIMEOUT = 1037

# Result Code Messages
RESULT_CODE_MESSAGES = {
    0: "Success",
    1032: "The customer cancelled the M-Pesa prompt",
    2001: "The customer entered the wrong M-Pesa PIN",
    1: "The customer does not have enough money in M-Pesa",
    17: "M-Pesa could not process this number",
    1037: "The customer's phone could not be reached or they did not respond in time",
    1001: "The customer has another M-Pesa payment in progress. Try again shortly",
    1019: "The payment request expired before the customer completed it",
    1025: "M-Pesa could not send the prompt. Try again",
    9999: "M-Pesa could not send the prompt. Try again",
}

# Daraja STK query answers with these error codes while the customer has not responded yet
MPESA_STILL_PROCESSING_CODES = {"500.001.1001"}

# ============================================================================
# Daraja API Endpoints
# ============================================================================

# Base URLs
DARAJASANDBOX_BASE_URL = "https://sandbox.safaricom.co.ke"
DARAJAPRODUCTION_BASE_URL = "https://api.safaricom.co.ke"

# Endpoints
OAUTH_TOKEN_ENDPOINT = "/oauth/v1/generate?grant_type=client_credentials"
STK_PUSH_ENDPOINT = "/mpesa/stkpush/v1/processrequest"
STK_PUSH_QUERY_ENDPOINT = "/mpesa/stkpushquery/v1/query"
B2C_ENDPOINT = "/mpesa/b2c/v1/paymentrequest"
B2B_ENDPOINT = "/mpesa/b2b/v1/paymentrequest"
C2B_REGISTER_URL_ENDPOINT = "/mpesa/c2b/v2/registerurl"

# ============================================================================
# Transaction Types
# ============================================================================

TRANSACTION_TYPE_PAYBILL = "CustomerPayBillOnline"
TRANSACTION_TYPE_BUYGOODS = "CustomerBuyGoodsOnline"

# ============================================================================
# Transaction Status
# ============================================================================

STATUS_PENDING = "Pending"
STATUS_SUCCESS = "Success"
STATUS_FAILED = "Failed"
STATUS_CANCELLED = "Cancelled"

# ============================================================================
# Shortcode Types
# ============================================================================

SHORTCODE_TYPE_PAYBILL = "Paybill"
SHORTCODE_TYPE_BUYGOODS = "BuyGoods"

# ============================================================================
# B2C Command IDs
# ============================================================================

B2C_COMMAND_SALARY_PAYMENT = "SalaryPayment"
B2C_COMMAND_BUSINESS_PAYMENT = "BusinessPayment"
B2C_COMMAND_PROMOTION_PAYMENT = "PromotionPayment"

# ============================================================================
# B2B Command IDs
# ============================================================================

B2B_COMMAND_BUSINESS_PAY_BILL = "BusinessPayBill"
B2B_COMMAND_BUSINESS_BUY_GOODS = "BusinessBuyGoods"

# ============================================================================
# Identifier Types
# ============================================================================

IDENTIFIER_TYPE_MSISDN = "1"
IDENTIFIER_TYPE_TILL_NUMBER = "2"
IDENTIFIER_TYPE_SHORTCODE = "4"

# ============================================================================
# Helper Functions
# ============================================================================

def get_result_message(result_code):
    """Get user-friendly message for Daraja result code"""
    return RESULT_CODE_MESSAGES.get(result_code, f"Unknown result code: {result_code}")

def is_success(result_code):
    """Check if result code indicates success"""
    return result_code == MPESA_SUCCESS

def get_base_url(environment):
    """Get Daraja base URL based on environment"""
    if environment == "Production":
        return DARAJAPRODUCTION_BASE_URL
    return DARAJASANDBOX_BASE_URL

