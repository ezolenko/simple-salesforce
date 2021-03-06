""" Classes for interacting with Salesforce Bulk API """

try:
    from collections import OrderedDict
except ImportError:
    # Python < 2.7
    from ordereddict import OrderedDict

import json
import requests
import re
from time import sleep
from simple_salesforce.util import SalesforceError

class SFBulkHandler(object):
    """ Bulk API request handler
    Intermediate class which allows us to use commands,
     such as 'sf.bulk.Contacts.create(...)'
    This is really just a middle layer, whose sole purpose is
    to allow the above syntax
    """

    def __init__(self, session_id, bulk_url, proxies=None, session=None):
        """Initialize the instance with the given parameters.

        Arguments:

        * session_id -- the session ID for authenticating to Salesforce
        * bulk_url -- API endpoint set in Salesforce instance
        * proxies -- the optional map of scheme to proxy server
        * session -- Custom requests session, created in calling code. This
                     enables the use of requests Session features not otherwise
                     exposed by simple_salesforce.
        """
        self.session_id = session_id
        self.session = session or requests.Session()
        self.bulk_url = bulk_url
        # don't wipe out original proxies with None
        if not session and proxies is not None:
            self.session.proxies = proxies

        # Define these headers separate from Salesforce class,
        # as bulk uses a slightly different format
        self.headers = {
            'Content-Type': 'application/json',
            'X-SFDC-Session': self.session_id,
            'X-PrettyPrint': '1'
        }

    def __getattr__(self, name):
        return SFBulkType(object_name=name, bulk_url=self.bulk_url,
                          headers=self.headers, session=self.session)

class SFBulkType(object):
    """ Interface to Bulk/Async API functions"""

    def __init__(self, object_name, bulk_url, headers, session):
        """Initialize the instance with the given parameters.

        Arguments:

        * object_name -- the name of the type of SObject this represents,
                         e.g. `Lead` or `Contact`
        * bulk_url -- API endpoint set in Salesforce instance
        * headers -- bulk API headers
        * session -- Custom requests session, created in calling code. This
                     enables the use of requests Session features not otherwise
                     exposed by simple_salesforce.
        """
        self.object_name = object_name
        self.bulk_url = bulk_url
        self.session = session
        self.headers = headers

    def _create_job(self, operation, object_name, external_id_field=None):
        """ Create a bulk job

        Arguments:

        * operation -- Bulk operation to be performed by job
        * object_name -- SF object
        * external_id_field -- unique identifier field for upsert operations
        """

        payload = {
            'operation': operation,
            'object': object_name,
            'contentType': 'JSON'
        }

        if operation == 'upsert':
            payload['externalIdFieldName'] = external_id_field

        url = "{}{}".format(self.bulk_url, 'job')

        result = _call_salesforce(url=url, method='POST', session=self.session,
                                  headers=self.headers,
                                  data=json.dumps(payload))
        return result.json(object_pairs_hook=OrderedDict)

    def _close_job(self, job_id):
        """ Close a bulk job """

        payload = {
            'state': 'Closed'
        }

        url = "{}{}{}".format(self.bulk_url, 'job/', job_id)

        result = _call_salesforce(url=url, method='POST', session=self.session,
                                  headers=self.headers,
                                  data=json.dumps(payload))
        return result.json(object_pairs_hook=OrderedDict)

    def _get_job(self, job_id):
        """ Get an existing job to check the status """

        url = "{}{}{}".format(self.bulk_url, 'job/', job_id)

        result = _call_salesforce(url=url, method='GET', session=self.session,
                                  headers=self.headers)
        return result.json(object_pairs_hook=OrderedDict)

    def _add_batch(self, job_id, data, operation):
        """ Add a set of data as a batch to an existing job
        Separating this out in case of later
        implementations involving multiple batches
        """

        url = "{}{}{}{}".format(self.bulk_url, 'job/', job_id, '/batch')

        if operation != 'query':
            data = json.dumps(data)

        result = _call_salesforce(url=url, method='POST', session=self.session,
                                  headers=self.headers, data=data)
        return result.json(object_pairs_hook=OrderedDict)

    def _get_batch(self, job_id, batch_id):
        """ Get an existing batch to check the status """

        url = "{}{}{}{}{}".format(self.bulk_url, 'job/',
                                  job_id, '/batch/', batch_id)

        result = _call_salesforce(url=url, method='GET', session=self.session,
                                  headers=self.headers)
        return result.json(object_pairs_hook=OrderedDict)

    def _get_batch_results(self, job_id, batch_id, operation):
        """ retrieve a set of results from a completed job """

        url = "{}{}{}{}{}{}".format(self.bulk_url, 'job/', job_id, '/batch/',
                                    batch_id, '/result')

        result = _call_salesforce(url=url, method='GET', session=self.session,
                                  headers=self.headers)

        if operation == 'query':
            result_data = result.json()
            if len(result_data) > 0:
                url_query_results = "{}{}{}".format(url, '/', result.json()[0])
                query_result = _call_salesforce(url=url_query_results, method='GET',
                                            session=self.session,
                                            headers=self.headers)
                return query_result.json()
            else:
                return result_data

        j = result

        text = result.text
        pattern = re.compile(r"Expecting ',' delimiter: line [0-9]* column [0-9]* .char ([0-9]*).")

        while True:
            try:
                j = json.loads(text)
                break
            except ValueError as e:
                print(e)
                match = pattern.fullmatch(str(e))
                if match is not None:
                    pos = int(match.group(1))
                    text = "{},{}".format(text[:pos], text[pos:])
        return j

    def _bulk_operation(self, object_name, operation, data,
                        external_id_field=None, wait=5): #pylint: disable=R0913
        """ String together helper functions to create a complete
        end-to-end bulk API request

        Arguments:

        * object_name -- SF object
        * operation -- Bulk operation to be performed by job
        * data -- list of dict to be passed as a batch
        * external_id_field -- unique identifier field for upsert operations
        * wait -- seconds to sleep between checking batch status
        """

        job = self._create_job(object_name=object_name, operation=operation,
                               external_id_field=external_id_field)

        batch = self._add_batch(job_id=job['id'], data=data,
                                operation=operation)

        self._close_job(job_id=job['id'])

        batch_status = self._get_batch(job_id=batch['jobId'],
                                       batch_id=batch['id'])['state']

        while batch_status not in ['Completed', 'Failed', 'Not Processed']:
            sleep(wait)
            batch_status = self._get_batch(job_id=batch['jobId'],
                                           batch_id=batch['id'])['state']

        results = self._get_batch_results(job_id=batch['jobId'],
                                          batch_id=batch['id'],
                                          operation=operation)
        return results

    # _bulk_operation wrappers to expose supported Salesforce bulk operations
    def delete(self, data):
        """ soft delete records """
        results = self._bulk_operation(object_name=self.object_name,
                                       operation='delete', data=data)
        return results

    def insert(self, data):
        """ insert records """
        results = self._bulk_operation(object_name=self.object_name,
                                       operation='insert', data=data)
        return results

    def upsert(self, data, external_id_field):
        """ upsert records based on a unique identifier """
        results = self._bulk_operation(object_name=self.object_name,
                                       operation='upsert',
                                       external_id_field=external_id_field,
                                       data=data)
        return results

    def update(self, data):
        """ update records """
        results = self._bulk_operation(object_name=self.object_name,
                                       operation='update', data=data)
        return results

    def hard_delete(self, data):
        """ hard delete records """
        results = self._bulk_operation(object_name=self.object_name,
                                       operation='hardDelete', data=data)
        return results

    def query(self, data):
        """ bulk query """
        results = self._bulk_operation(object_name=self.object_name,
                                       operation='query', data=data)
        return results

# TODO: refactor _call_salesforce, _exception_handler,
#       and exception classes into util.py for common
#       access between different API handlers

def _call_salesforce(url, method, session, headers, **kwargs):
    """Utility method for performing HTTP call to Salesforce.

    Returns a `requests.result` object.
    """

    additional_headers = kwargs.pop('additional_headers', dict())
    headers.update(additional_headers or dict())
    result = session.request(method, url, headers=headers, **kwargs)

    if result.status_code >= 300:
        _exception_handler(result)

    return result


def _exception_handler(result, name=""):
    """Exception router. Determines which error to raise for bad results"""
    try:
        response_content = result.json()
    # pylint: disable=broad-except
    except Exception:
        response_content = result.text

    exc_map = {
        300: SalesforceMoreThanOneRecord,
        400: SalesforceMalformedRequest,
        401: SalesforceExpiredSession,
        403: SalesforceRefusedRequest,
        404: SalesforceResourceNotFound,
    }
    exc_cls = exc_map.get(result.status_code, SalesforceGeneralError)

    raise exc_cls(result.url, result.status_code, name, response_content)


class SalesforceMoreThanOneRecord(SalesforceError):
    """
    Error Code: 300
    The value returned when an external ID exists in more than one record. The
    response body contains the list of matching records.
    """
    message = u"More than one record for {url}. Response content: {content}"


class SalesforceMalformedRequest(SalesforceError):
    """
    Error Code: 400
    The request couldn't be understood, usually becaue the JSON or XML body
    contains an error.
    """
    message = u"Malformed request {url}. Response content: {content}"


class SalesforceExpiredSession(SalesforceError):
    """
    Error Code: 401
    The session ID or OAuth token used has expired or is invalid. The response
    body contains the message and errorCode.
    """
    message = u"Expired session for {url}. Response content: {content}"


class SalesforceRefusedRequest(SalesforceError):
    """
    Error Code: 403
    The request has been refused. Verify that the logged-in user has
    appropriate permissions.
    """
    message = u"Request refused for {url}. Response content: {content}"


class SalesforceResourceNotFound(SalesforceError):
    """
    Error Code: 404
    The requested resource couldn't be found. Check the URI for errors, and
    verify that there are no sharing issues.
    """
    message = u'Resource {name} Not Found. Response content: {content}'

    def __str__(self):
        return self.message.format(name=self.resource_name,
                                   content=self.content)


class SalesforceGeneralError(SalesforceError):
    """
    A non-specific Salesforce error.
    """
    message = u'Error Code {status}. Response content: {content}'

    def __str__(self):
        return self.message.format(status=self.status, content=self.content)
