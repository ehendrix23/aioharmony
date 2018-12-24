"""
Define the exception classes for aioharmony module.
"""

class HarmonyClientException(Exception):
    """
    Root class exception
    """


class HarmonyClientTimeOutException(HarmonyClientException, TimeoutError):
    """
    Raised on timeouts
    """
