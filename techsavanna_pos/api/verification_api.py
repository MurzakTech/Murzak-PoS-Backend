"""
Email and Phone Verification API
Handles OTP-based verification for user registration
"""

import secrets
import string
from datetime import datetime, timedelta
from typing import Dict, Optional

import frappe
from frappe import _
from frappe.utils import now_datetime, add_to_date


# Verification code configuration
OTP_LENGTH = 6
OTP_EXPIRY_MINUTES = 15
MAX_ATTEMPTS = 3
RESEND_COOLDOWN_SECONDS = 60  # 1 minute between resends


def generate_otp_code(length: int = OTP_LENGTH) -> str:
    """Generate a random numeric OTP code
    
    Args:
        length: Length of OTP code (default: 6)
        
    Returns:
        OTP code as string
    """
    return ''.join(secrets.choice(string.digits) for _ in range(length))


def get_verification_key(identifier: str, verification_type: str) -> str:
    """Get cache key for verification code
    
    Args:
        identifier: Email or phone number
        verification_type: 'email' or 'phone'
        
    Returns:
        Cache key string
    """
    return f"verification_{verification_type}_{identifier}"


def get_attempts_key(identifier: str, verification_type: str) -> str:
    """Get cache key for verification attempts
    
    Args:
        identifier: Email or phone number
        verification_type: 'email' or 'phone'
        
    Returns:
        Cache key string
    """
    return f"verification_attempts_{verification_type}_{identifier}"


def get_resend_key(identifier: str, verification_type: str) -> str:
    """Get cache key for resend cooldown
    
    Args:
        identifier: Email or phone number
        verification_type: 'email' or 'phone'
        
    Returns:
        Cache key string
    """
    return f"verification_resend_{verification_type}_{identifier}"


@frappe.whitelist(allow_guest=True)
def send_email_verification(email: str) -> Dict:
    """Send email verification code
    
    Args:
        email: Email address to verify
        
    Returns:
        Success message
    """
    # Validate email format
    if not email or "@" not in email:
        frappe.throw(_("Please provide a valid email address"), frappe.ValidationError)
    
    # Check if email is already registered
    if frappe.db.exists("User", email):
        frappe.throw(_("Email address is already registered"), frappe.ValidationError)
    
    # Check resend cooldown
    resend_key = get_resend_key(email, "email")
    last_sent = frappe.cache().get(resend_key)
    if last_sent:
        remaining = RESEND_COOLDOWN_SECONDS - (now_datetime() - last_sent).total_seconds()
        if remaining > 0:
            frappe.throw(
                _("Please wait {0} seconds before requesting a new verification code").format(int(remaining)),
                frappe.ValidationError
            )
    
    # Generate OTP code
    otp_code = generate_otp_code()
    expiry_time = add_to_date(None, minutes=OTP_EXPIRY_MINUTES, as_datetime=True)
    
    # Store verification code in cache with expiry
    cache_key = get_verification_key(email, "email")
    frappe.cache().setex(
        cache_key,
        OTP_EXPIRY_MINUTES * 60,  # Expiry in seconds
        {
            "code": otp_code,
            "email": email,
            "expires_at": expiry_time.isoformat(),
            "verified": False
        }
    )
    
    # Reset attempts counter
    attempts_key = get_attempts_key(email, "email")
    frappe.cache().delete(attempts_key)
    
    # Set resend cooldown
    frappe.cache().setex(resend_key, RESEND_COOLDOWN_SECONDS, now_datetime())
    
    # Send verification email
    try:
        subject = _("Email Verification Code")
        message = _("""
            <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
                <h2 style="color: #333;">Email Verification</h2>
                <p>Hello,</p>
                <p>Thank you for registering with SavvyPOS. Please use the verification code below to verify your email address:</p>
                <div style="background-color: #f5f5f5; padding: 20px; text-align: center; margin: 20px 0; border-radius: 5px;">
                    <h1 style="color: #007bff; margin: 0; font-size: 32px; letter-spacing: 5px;">{otp_code}</h1>
                </div>
                <p>This code will expire in {expiry_minutes} minutes.</p>
                <p>If you did not request this verification code, please ignore this email.</p>
                <p style="color: #666; font-size: 12px; margin-top: 30px;">Best regards,<br>SavvyPOS Team</p>
            </div>
        """).format(otp_code=otp_code, expiry_minutes=OTP_EXPIRY_MINUTES)
        
        frappe.sendmail(
            recipients=email,
            subject=subject,
            message=message,
            header=[_("Email Verification"), "blue"],
            delayed=False,
            retry=3,
        )
    except Exception as e:
        frappe.log_error(f"Error sending email verification to {email}: {str(e)}", "Email Verification Error")
        frappe.throw(
            _("Failed to send verification email. Please try again later or contact support."),
            frappe.ValidationError
        )
    
    return {
        "success": True,
        "message": _("Verification code has been sent to your email address"),
        "expires_in_minutes": OTP_EXPIRY_MINUTES
    }


@frappe.whitelist(allow_guest=True)
def send_phone_verification(phone: str) -> Dict:
    """Send phone verification code via SMS
    
    Args:
        phone: Phone number to verify (with country code, e.g., +254712345678)
        
    Returns:
        Success message
    """
    # Validate phone format (basic validation)
    if not phone or len(phone) < 10:
        frappe.throw(_("Please provide a valid phone number"), frappe.ValidationError)
    
    # Check if phone is already registered
    existing_user = frappe.db.get_value("User", {"mobile_no": phone}, "name")
    if existing_user:
        frappe.throw(_("Phone number is already registered"), frappe.ValidationError)
    
    # Check resend cooldown
    resend_key = get_resend_key(phone, "phone")
    last_sent = frappe.cache().get(resend_key)
    if last_sent:
        remaining = RESEND_COOLDOWN_SECONDS - (now_datetime() - last_sent).total_seconds()
        if remaining > 0:
            frappe.throw(
                _("Please wait {0} seconds before requesting a new verification code").format(int(remaining)),
                frappe.ValidationError
            )
    
    # Generate OTP code
    otp_code = generate_otp_code()
    expiry_time = add_to_date(None, minutes=OTP_EXPIRY_MINUTES, as_datetime=True)
    
    # Store verification code in cache with expiry
    cache_key = get_verification_key(phone, "phone")
    frappe.cache().setex(
        cache_key,
        OTP_EXPIRY_MINUTES * 60,  # Expiry in seconds
        {
            "code": otp_code,
            "phone": phone,
            "expires_at": expiry_time.isoformat(),
            "verified": False
        }
    )
    
    # Reset attempts counter
    attempts_key = get_attempts_key(phone, "phone")
    frappe.cache().delete(attempts_key)
    
    # Set resend cooldown
    frappe.cache().setex(resend_key, RESEND_COOLDOWN_SECONDS, now_datetime())
    
    # Send verification SMS
    try:
        from frappe.core.doctype.sms_settings.sms_settings import send_sms
        
        message = _("Your SavvyPOS verification code is: {otp_code}. Valid for {expiry_minutes} minutes.").format(
            otp_code=otp_code,
            expiry_minutes=OTP_EXPIRY_MINUTES
        )
        
        # Check if SMS settings are configured
        sms_settings = frappe.get_doc("SMS Settings", "SMS Settings", ignore_permissions=True)
        if not sms_settings.sms_gateway_url:
            frappe.throw(
                _("SMS service is not configured. Please contact support for phone verification."),
                frappe.ValidationError
            )
        
        # Send SMS
        send_sms([phone], message)
        
    except Exception as e:
        frappe.log_error(f"Error sending SMS verification to {phone}: {str(e)}", "SMS Verification Error")
        # If SMS fails, we can still allow registration but log the error
        # Or throw an error depending on requirements
        frappe.throw(
            _("Failed to send verification SMS. Please try again later or contact support."),
            frappe.ValidationError
        )
    
    return {
        "success": True,
        "message": _("Verification code has been sent to your phone number"),
        "expires_in_minutes": OTP_EXPIRY_MINUTES
    }


@frappe.whitelist(allow_guest=True)
def verify_email_code(email: str, code: str) -> Dict:
    """Verify email verification code
    
    Args:
        email: Email address
        code: Verification code
        
    Returns:
        Verification result
    """
    if not email or not code:
        frappe.throw(_("Email and verification code are required"), frappe.ValidationError)
    
    # Check attempts
    attempts_key = get_attempts_key(email, "email")
    attempts = frappe.cache().get(attempts_key) or 0
    
    if attempts >= MAX_ATTEMPTS:
        frappe.throw(
            _("Maximum verification attempts exceeded. Please request a new verification code."),
            frappe.ValidationError
        )
    
    # Get verification data
    cache_key = get_verification_key(email, "email")
    verification_data = frappe.cache().get(cache_key)
    
    if not verification_data:
        frappe.throw(
            _("Verification code not found or expired. Please request a new verification code."),
            frappe.ValidationError
        )
    
    # Check if already verified
    if verification_data.get("verified"):
        return {
            "success": True,
            "message": _("Email has already been verified"),
            "verified": True
        }
    
    # Check expiry
    expires_at = datetime.fromisoformat(verification_data["expires_at"])
    if now_datetime() > expires_at:
        frappe.cache().delete(cache_key)
        frappe.throw(
            _("Verification code has expired. Please request a new verification code."),
            frappe.ValidationError
        )
    
    # Verify code
    if verification_data["code"] != code:
        # Increment attempts
        frappe.cache().setex(attempts_key, OTP_EXPIRY_MINUTES * 60, attempts + 1)
        remaining_attempts = MAX_ATTEMPTS - (attempts + 1)
        
        if remaining_attempts > 0:
            frappe.throw(
                _("Invalid verification code. {0} attempt(s) remaining.").format(remaining_attempts),
                frappe.ValidationError
            )
        else:
            frappe.throw(
                _("Invalid verification code. Maximum attempts exceeded. Please request a new verification code."),
                frappe.ValidationError
            )
    
    # Mark as verified
    verification_data["verified"] = True
    verification_data["verified_at"] = now_datetime().isoformat()
    
    # Store verified status (extend expiry to 24 hours for verified status)
    frappe.cache().setex(cache_key, 24 * 60 * 60, verification_data)
    
    return {
        "success": True,
        "message": _("Email verified successfully"),
        "verified": True
    }


@frappe.whitelist(allow_guest=True)
def verify_phone_code(phone: str, code: str) -> Dict:
    """Verify phone verification code
    
    Args:
        phone: Phone number
        code: Verification code
        
    Returns:
        Verification result
    """
    if not phone or not code:
        frappe.throw(_("Phone number and verification code are required"), frappe.ValidationError)
    
    # Check attempts
    attempts_key = get_attempts_key(phone, "phone")
    attempts = frappe.cache().get(attempts_key) or 0
    
    if attempts >= MAX_ATTEMPTS:
        frappe.throw(
            _("Maximum verification attempts exceeded. Please request a new verification code."),
            frappe.ValidationError
        )
    
    # Get verification data
    cache_key = get_verification_key(phone, "phone")
    verification_data = frappe.cache().get(cache_key)
    
    if not verification_data:
        frappe.throw(
            _("Verification code not found or expired. Please request a new verification code."),
            frappe.ValidationError
        )
    
    # Check if already verified
    if verification_data.get("verified"):
        return {
            "success": True,
            "message": _("Phone number has already been verified"),
            "verified": True
        }
    
    # Check expiry
    expires_at = datetime.fromisoformat(verification_data["expires_at"])
    if now_datetime() > expires_at:
        frappe.cache().delete(cache_key)
        frappe.throw(
            _("Verification code has expired. Please request a new verification code."),
            frappe.ValidationError
        )
    
    # Verify code
    if verification_data["code"] != code:
        # Increment attempts
        frappe.cache().setex(attempts_key, OTP_EXPIRY_MINUTES * 60, attempts + 1)
        remaining_attempts = MAX_ATTEMPTS - (attempts + 1)
        
        if remaining_attempts > 0:
            frappe.throw(
                _("Invalid verification code. {0} attempt(s) remaining.").format(remaining_attempts),
                frappe.ValidationError
            )
        else:
            frappe.throw(
                _("Invalid verification code. Maximum attempts exceeded. Please request a new verification code."),
                frappe.ValidationError
            )
    
    # Mark as verified
    verification_data["verified"] = True
    verification_data["verified_at"] = now_datetime().isoformat()
    
    # Store verified status (extend expiry to 24 hours for verified status)
    frappe.cache().setex(cache_key, 24 * 60 * 60, verification_data)
    
    return {
        "success": True,
        "message": _("Phone number verified successfully"),
        "verified": True
    }


@frappe.whitelist(allow_guest=True)
def check_verification_status(identifier: str, verification_type: str) -> Dict:
    """Check if email or phone is verified
    
    Args:
        identifier: Email or phone number
        verification_type: 'email' or 'phone'
        
    Returns:
        Verification status
    """
    if verification_type not in ["email", "phone"]:
        frappe.throw(_("Verification type must be 'email' or 'phone'"), frappe.ValidationError)
    
    cache_key = get_verification_key(identifier, verification_type)
    verification_data = frappe.cache().get(cache_key)
    
    if not verification_data:
        return {
            "verified": False,
            "message": _("No verification found for this {0}").format(verification_type)
        }
    
    verified = verification_data.get("verified", False)
    
    if verified:
        return {
            "verified": True,
            "message": _("{0} has been verified").format(verification_type.capitalize()),
            "verified_at": verification_data.get("verified_at")
        }
    else:
        expires_at = datetime.fromisoformat(verification_data["expires_at"])
        if now_datetime() > expires_at:
            return {
                "verified": False,
                "message": _("Verification code has expired")
            }
        else:
            return {
                "verified": False,
                "message": _("Verification pending"),
                "expires_at": verification_data["expires_at"]
            }

