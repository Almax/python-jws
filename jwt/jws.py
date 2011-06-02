import re
import utils

# Exceptions
class DecodeError(Exception): pass
class InvalidHeaderError(Exception): pass
class SignatureError(Exception): pass
class MissingAlgorithmError(Exception): pass

# Signing algorithms
class SigningAlgorithm(object):
    """Base for algorithm support classes."""
    supported_bits = (256, 384, 512)
    def __init__(self, bits):
        """
        Determine if the algorithm supports the requested bit depth and set up
        matching hash method from ``hashlib`` if necessary.
        """
        self.bits = int(bits)
        if self.bits not in self.supported_bits:
            raise NotImplementedError("%s implements %s bit algorithms (given %d)" %
                                      (self.__class__, ', '.join(self.supported_bits), self.bits))
        if not getattr(self, 'hasher', None):
            import hashlib
            self.hasher = getattr(hashlib, 'sha%d' % self.bits)

class HMAC(SigningAlgorithm):
    """
    Support for HMAC signing.
    """
    def sign(self, msg, key):
        import hmac
        utfkey = unicode(key).encode('utf8')
        return hmac.new(utfkey, msg, self.hasher).digest()

    def verify(self, msg, crypto, key):
        if not self.sign(msg, key) == crypto:
            raise SignatureError("Could not validate signature")
        return True

class RSA(SigningAlgorithm):
    """
    Support for RSA signing.

    The ``Crypto`` package is required. However...

    NOTE: THIS ALGORITHM IS CRIPPLED AND INCOMPLETE

    Section 7.2 of the specification (found at
    http://self-issued.info/docs/draft-jones-json-web-signature.html)
    describes the algorithm for creating a JWS with RSA. It is mandatory to
    use RSASSA-PKCS1-V1_5-SIGN and either SHA256, 385 or 512.

    Problem 1: The Crypto library doesn't currently support PKCS1-V1_5. There
    is a fork that does have support:

    https://github.com/Legrandin/pycrypto/tree/pkcs1

    Problem 2: The PKCS signing method requires a Crypto.Hash class.
    Crypto.Hash doesn't yet have support anything above SHA256.

    Bottom line, you should probably use ECDSA instead.
    """
    supported_bits = (256,)

    def sign(self, msg, key):
        """
        Signs a message with an RSA PrivateKey and hash method
        """
        import Crypto.Signature.PKCS1_v1_5 as PKCS
        import Crypto.Hash.SHA256 as SHA256
        import Crypto.PublicKey.RSA as RSA

        hashm = SHA256.new()
        hashm.update(msg)
        private_key = RSA.importKey(key)
        return PKCS.sign(hashm, private_key)

    def verify(self, msg, crypto, key):
        """
        Verifies a message using RSA cryptographic signature and key.

        ``crypto`` is the cryptographic signature
        ``key`` is the verifying key. Can be a real key object or a string.
        """
        import Crypto.Signature.PKCS1_v1_5 as PKCS
        import Crypto.Hash.SHA256 as SHA256
        import Crypto.PublicKey.RSA as RSA

        hashm = SHA256.new()
        hashm.update(msg)
        private_key = key
        if not isinstance(key, RSA._RSAobj):
            private_key = RSA.importKey(key)
        if not PKCS.verify(hashm, private_key, crypto):
            raise SignatureError("Could not validate signature")
        return True

class ECDSA(SigningAlgorithm):
    """
    Support for ECDSA signing. This is the preferred algorithm for private/public key
    verification.

    The ``ecdsa`` package is required. ``pip install ecdsa``
    """
    bits_to_curve = {
        256: 'NIST256p',
        384: 'NIST384p',
        512: 'NIST521p',
    }
    def sign(self, msg, key):
        """
        Signs a message with an ECDSA SigningKey and hash method matching the
        bit depth of curve algorithm.
        """
        import ecdsa
        curve = getattr(ecdsa, self.bits_to_curve[self.bits])
        signing_key = ecdsa.SigningKey.from_string(key, curve=curve)
        return signing_key.sign(msg, hashfunc=self.hasher)

    def verify(self, msg, crypto, key):
        """
        Verifies a message using ECDSA cryptographic signature and key.

        ``crypto`` is the cryptographic signature
        ``key`` is the verifying key. Can be a real key object or a string.
        """
        import ecdsa
        curve = getattr(ecdsa, self.bits_to_curve[self.bits])
        vk = key
        if not isinstance(vk, ecdsa.VerifyingKey):
            vk = ecdsa.VerifyingKey.from_string(key, curve=curve)
        try:
            vk.verify(crypto, msg, hashfunc=self.hasher)
        except ecdsa.BadSignatureError, e:
            raise SignatureError("Could not validate signature")
        return True

# Main class
class JWS(object):
    reserved_params = [
        'alg', # REQUIRED, signing algo, see signing_methods
        'typ', # OPTIONAL, type of signed content
        'jku', # OPTIONAL, JSON Key URL. See http://self-issued.info/docs/draft-jones-json-web-key.html
        'kid', # OPTIONAL, key id, hint for which key to use.
        'x5u', # OPTIONAL, x.509 URL pointing to certificate or certificate chain
        'x5t', # OPTIONAL, x.509 certificate thumbprint
    ]

    def __init__(self, header={}, payload={}):
        self.algorithms = [
            (r'^HS(256|384|512)$', HMAC),
            (r'^RS(256|384|512)$', RSA),
            (r'^ES(256|384|512)$', ECDSA),
        ]
        self.__algorithm = None
        if header:  self.set_header(header)
        if payload: self.set_payload(payload)

    def set_header(self, header):
        """
        Verify and set the header. Also calls set_algorithm when it finds an
        alg property and ensures that the algorithm is implemented.
        """
        if u'alg' not in header:
            raise InvalidHeaderError('JWS Header Input must have alg parameter')
        self.set_algorithm(header['alg'])
        self.__header = header

    def set_payload(self, payload):
        """For symmetry"""
        self.__payload = payload

    def set_algorithm(self, algo):
        """
        Verify and set the signing/verifying algorithm. Looks up regex mapping
        from self.algorithms to determine which algorithm class to use.
        """
        for (regex, cls) in self.algorithms:
            match = re.match(regex, algo)
            if match:
                self.__algorithm = cls(match.groups()[0])
                return
        raise NotImplementedError("Could not find algorithm defined for %s" % algo)

    def sign(self, *args, **kwargs):
        """
        Calls the sign method on the algorithm instance determined from the
        header with signing input, generated from header and payload, and any
        additional algorithm-specific parameters.

        Returns the resulting signature as a base64url encoded string.
        """
        if not self.__algorithm:
            raise MissingAlgorithmError("Could not find algorithm. Make sure to call set_header() before trying to sign anything")
        crypto = self.__algorithm.sign(self.signing_input(), *args, **kwargs)
        return utils.base64url_encode(crypto)

    def verify(self, crypto_output, *args, **kwargs):
        """
        Calls the verify method on the algorithm instance determined from the
        header with signing input, generated from header and payload, the
        signature to verify, and any additional algorithm-specific parameters.
        """
        if not self.__algorithm:
            raise MissingAlgorithmError("Could not find algorithm. Make sure to call set_header() before trying to verify anything")
        crypto = utils.base64url_decode(crypto_output)
        return self.__algorithm.verify(self.signing_input(), crypto, *args, **kwargs)

    def signing_input(self):
        """
        Generates the signing input by json + base64url encoding the header
        and the payload, then concatenating the results with a '.' character.
        """
        header_input, payload_input = map(utils.encode, [self.__header, self.__payload])
        return "%s.%s" % (header_input, payload_input)



import hashlib
####################
# semi-private api #
####################
def _hmac_sign(bits):
    print bits
def _hmac_verify(bits):
    print bits

def _rsa_sign(bits):
    print bits
def _rsa_verify(bits):
    print bits

def _ecdsa_sign(bits):
    print bits
def _ecdsa_verify(bits):
    print bits

##############
# public api #
##############
ALGORITHMS = {
    'HS256': { 'sign': _hmac_sign(256), 'verify': _hmac_verify(256), },
    'HS384': { 'sign': _hmac_sign(384), 'verify': _hmac_verify(384), },
    'HS512': { 'sign': _hmac_sign(512), 'verify': _hmac_verify(512), },
        
    'RS256': { 'sign': _rsa_sign(256), 'verify': _rsa_verify(256), },
    'RS384': { 'sign': _rsa_sign(384), 'verify': _rsa_verify(384), },
    'RS512': { 'sign': _rsa_sign(512), 'verify': _rsa_verify(512), },

    'ES256': { 'sign': _ecdsa_sign(256), 'verify': _ecdsa_verify(256), },
    'ES384': { 'sign': _ecdsa_sign(384), 'verify': _ecdsa_verify(384), },
    'ES512': { 'sign': _ecdsa_sign(512), 'verify': _ecdsa_verify(512), },
}

def sign(header, payload, algos=ALGORITHMS): pass
def verify(header, payload, signature, algos=ALGORITHMS): pass
