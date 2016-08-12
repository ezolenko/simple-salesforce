"""Login classes and functions for Simple-Salesforce

Heavily Modified from RestForce 1.0.0
"""

DEFAULT_CLIENT_ID_PREFIX = 'RestForce'


from simple_salesforce.api import DEFAULT_API_VERSION
from simple_salesforce.util import getUniqueElementValueFromXmlString
from simple_salesforce.util import SalesforceError
try:
    # Python 3+
    from html import escape
except ImportError:
    from cgi import escape
import requests

# pylint: disable=invalid-name
def cleanseInstanceUrl(instance_url):
    """Remove some common/likely noise from an instance url"""
    return (instance_url
            .replace('http://', '')
            .replace('https://', '')
            .split('/')[0]
            .replace('-api', ''))

# pylint: disable=invalid-name,too-many-arguments,too-many-locals
def SalesforceLogin(
        username=None, password=None, security_token=None,
        refresh_token=None, consumer_id=None, consumer_secret=None,
        organizationId=None, sandbox=False, sf_version=DEFAULT_API_VERSION,
        proxies=None, session=None, client_id=None):
    """Return a tuple of `(session_id, sf_instance)` where `session_id` is the
    session ID to use for authentication to Salesforce and `sf_instance` is
    the domain of the instance of Salesforce to use for the session.

    Arguments:

    * username -- the Salesforce username to use for authentication
    * password -- the password for the username
    * security_token -- the security token for the username

        NOTE: next 3 parameters are optional, but should all be passed if
            logging in with Connected App and refresh token

    * refresh_token -- the refresh token provided to the Connected App (used in
        some OAuth schemes)
    * consumer_id -- the consumer ID for the Connected App that was granted
        user's refresh token
    * consumer_secret -- the consumer secret for the Connected App that was
        granted the user's refresh token.


    * organizationId -- the ID of your organization
            NOTE: security_token an organizationId are mutually exclusive
    * sandbox -- True if you want to login to `test.salesforce.com`, False if
                 you want to login to `login.salesforce.com`.
    * sf_version -- the version of the Salesforce API to use, for example
                    "27.0"
    * proxies -- the optional map of scheme to proxy server
    * session -- Custom requests session, created in calling code. This
                 enables the use of requets Session features not otherwise
                 exposed by simple_salesforce.
    * client_id -- the ID of this client
    """

    soap_url = 'https://{domain}.salesforce.com/services/Soap/u/{sf_version}'
    rest_url = 'https://{domain}.salesforce.com/services/oauth2/token'
    domain = 'test' if sandbox else 'login'

    if client_id:
        client_id = "{prefix}/{app_name}".format(
            prefix=DEFAULT_CLIENT_ID_PREFIX,
            app_name=client_id)
    else:
        client_id = DEFAULT_CLIENT_ID_PREFIX

    soap_url = soap_url.format(domain=domain, sf_version=sf_version)
    rest_url = rest_url.format(domain=domain, sf_version=sf_version)


    # Let's see if this flow is appropriate first as it's quite different
    # than the rest of the flows
    if all(arg is not None for arg in (
        refresh_token, consumer_id, consumer_secret)):
        # Use client credentials and refresh_token provided to Connected App by
        # Salesforce during OAuth process to get a new session/access_token
        data = {
            'grant_type': 'refresh_token',
            'client_id' : consumer_id,
            'client_secret' : consumer_secret,
            'refresh_token': refresh_token
        }
        headers = {
            'content-type': 'application/x-www-form-urlencoded'
        }

        response = (session or requests).post(
            url=rest_url, data=data, headers=headers,
            proxies=proxies)

        response_data = response.json()

        if response.status_code != 200:
            # Something's gone wrong :(

            # TODO: Could there be a case where this isn't a list? Or the error
            # is not always in the first element? Not sure.
            if len(response_data) > 0:
                response_data = response_data[0]

            raise SalesforceAuthenticationFailed(
                response_data['errorCode'], response_data['message'])

        session_id = response_data.get('access_token')
        sf_instance = cleanseInstanceUrl(response_data.get('instance_url'))

        return session_id, sf_instance


    # pylint: disable=deprecated-method
    username = escape(username)
    # pylint: disable=deprecated-method
    password = escape(password)

    # Check if token authentication is used
    if security_token is not None:
        # Security Token Soap request body
        login_soap_request_body = """<?xml version="1.0" encoding="utf-8" ?>
        <env:Envelope
                xmlns:xsd="http://www.w3.org/2001/XMLSchema"
                xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
                xmlns:env="http://schemas.xmlsoap.org/soap/envelope/"
                xmlns:urn="urn:partner.soap.sforce.com">
            <env:Header>
                <urn:CallOptions>
                    <urn:client>{client_id}</urn:client>
                    <urn:defaultNamespace>sf</urn:defaultNamespace>
                </urn:CallOptions>
            </env:Header>
            <env:Body>
                <n1:login xmlns:n1="urn:partner.soap.sforce.com">
                    <n1:username>{username}</n1:username>
                    <n1:password>{password}{token}</n1:password>
                </n1:login>
            </env:Body>
        </env:Envelope>""".format(
            username=username, password=password, token=security_token,
            client_id=client_id)

    # Check if IP Filtering is used in conjuction with organizationId
    elif organizationId is not None:
        # IP Filtering Login Soap request body
        login_soap_request_body = """<?xml version="1.0" encoding="utf-8" ?>
        <soapenv:Envelope
                xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/"
                xmlns:urn="urn:partner.soap.sforce.com">
            <soapenv:Header>
                <urn:CallOptions>
                    <urn:client>{client_id}</urn:client>
                    <urn:defaultNamespace>sf</urn:defaultNamespace>
                </urn:CallOptions>
                <urn:LoginScopeHeader>
                    <urn:organizationId>{organizationId}</urn:organizationId>
                </urn:LoginScopeHeader>
            </soapenv:Header>
            <soapenv:Body>
                <urn:login>
                    <urn:username>{username}</urn:username>
                    <urn:password>{password}</urn:password>
                </urn:login>
            </soapenv:Body>
        </soapenv:Envelope>""".format(
            username=username, password=password, organizationId=organizationId,
            client_id=client_id)
    elif username is not None and password is not None:
        # IP Filtering for non self-service users
        login_soap_request_body = """<?xml version="1.0" encoding="utf-8" ?>
        <soapenv:Envelope
                xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/"
                xmlns:urn="urn:partner.soap.sforce.com">
            <soapenv:Header>
                <urn:CallOptions>
                    <urn:client>{client_id}</urn:client>
                    <urn:defaultNamespace>sf</urn:defaultNamespace>
                </urn:CallOptions>
            </soapenv:Header>
            <soapenv:Body>
                <urn:login>
                    <urn:username>{username}</urn:username>
                    <urn:password>{password}</urn:password>
                </urn:login>
            </soapenv:Body>
        </soapenv:Envelope>""".format(
            username=username, password=password, client_id=client_id)

    else:
        except_code = 'INVALID AUTH'
        except_msg = (
            'You must submit either a security token or organizationId for '
            'authentication'
        )
        raise SalesforceAuthenticationFailed(except_code, except_msg)

    login_soap_request_headers = {
        'content-type': 'text/xml',
        'charset': 'UTF-8',
        'SOAPAction': 'login'
    }
    response = (session or requests).post(
        soap_url, login_soap_request_body, headers=login_soap_request_headers,
        proxies=proxies)

    if response.status_code != 200:
        except_code = getUniqueElementValueFromXmlString(
            response.content, 'sf:exceptionCode')
        except_msg = getUniqueElementValueFromXmlString(
            response.content, 'sf:exceptionMessage')
        raise SalesforceAuthenticationFailed(except_code, except_msg)

    session_id = getUniqueElementValueFromXmlString(
        response.content, 'sessionId')
    server_url = getUniqueElementValueFromXmlString(
        response.content, 'serverUrl')

    sf_instance = cleanseInstanceUrl(server_url)

    return session_id, sf_instance


class SalesforceAuthenticationFailed(SalesforceError):
    """
    Thrown to indicate that authentication with Salesforce failed.
    """
    def __init__(self, code, message):
        # TODO exceptions don't seem to be using parent constructors at all.
        # this should be fixed.
        # pylint: disable=super-init-not-called
        self.code = code
        self.message = message

    def __str__(self):
        return u'{code}: {message}'.format(code=self.code,
                                           message=self.message)
