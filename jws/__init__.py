import utils

# local 
import algos
import header
import router

##############
# public api #
##############
def sign(head, payload, algos=None):
    processed = header.process(head, 'sign')

def verify(head, payload, signature, algos=None):
    processed = header.process(head, 'verify')
    
####################
# semi-private api #
####################
# header stuff

def _signing_input(header, payload):
    """
    Generates the signing input by json + base64url encoding the header
    and the payload, then concatenating the results with a '.' character.
    """
    header_input, payload_input = map(utils.encode, [header, payload])
    return "%s.%s" % (header_input, payload_input)

