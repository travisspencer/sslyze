#!/usr/bin/env python
#-------------------------------------------------------------------------------
# Name:         PluginOpenSSLCipherSuites.py
# Purpose:      Scans the target server for supported OpenSSL cipher suites.
#
# Author:       alban
#
# Copyright:    2012 SSLyze developers
#
#   SSLyze is free software: you can redistribute it and/or modify
#   it under the terms of the GNU General Public License as published by
#   the Free Software Foundation, either version 2 of the License, or
#   (at your option) any later version.
#
#   SSLyze is distributed in the hope that it will be useful,
#   but WITHOUT ANY WARRANTY; without even the implied warranty of
#   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#   GNU General Public License for more details.
#
#   You should have received a copy of the GNU General Public License
#   along with SSLyze.  If not, see <http://www.gnu.org/licenses/>.
#-------------------------------------------------------------------------------

from xml.etree.ElementTree import Element

from plugins import PluginBase
from utils.ThreadPool import ThreadPool
from utils.SSLyzeSSLConnection import create_sslyze_connection, SSLHandshakeRejected
from nassl import SSLV2, SSLV3, TLSV1, TLSV1_1, TLSV1_2
from nassl.SslClient import SslClient


class PluginOpenSSLCipherSuites(PluginBase.PluginBase):


    interface = PluginBase.PluginInterface(
        "PluginOpenSSLCipherSuites",
        "Scans the target server for supported OpenSSL cipher suites.")
    interface.add_command(
        command="sslv2",
        help="Lists the SSL 2.0 OpenSSL cipher suites supported by the server.",
        dest=None)
    interface.add_command(
        command="sslv3",
        help="Lists the SSL 3.0 OpenSSL cipher suites supported by the server.",
        dest=None)
    interface.add_command(
        command="tlsv1",
        help="Lists the TLS 1.0 OpenSSL cipher suites supported by the server.",
        dest=None)
    interface.add_command(
        command="tlsv1_1",
        help="Lists the TLS 1.1 OpenSSL cipher suites supported by the server.",
        dest=None)
    interface.add_command(
        command="tlsv1_2",
        help="Lists the TLS 1.2 OpenSSL cipher suites supported by the server.",
        dest=None)
    interface.add_option(
        option='http_get',
        help="Option - For each cipher suite, sends an HTTP GET request after "
        "completing the SSL handshake and returns the HTTP status code.",
        dest=None)
    interface.add_option(
        option='hide_rejected_ciphers',
        help="Option - Hides the (usually long) list of cipher suites that were"
        " rejected by the server.",
        dest=None)


    def process_task(self, target, command, args):

        MAX_THREADS = 30
        sslVersionDict = {'sslv2': SSLV2,
                       'sslv3': SSLV3,
                       'tlsv1': TLSV1,
                       'tlsv1_1': TLSV1_1,
                       'tlsv1_2': TLSV1_2}
        try:
            sslVersion = sslVersionDict[command]
        except KeyError:
            raise Exception("PluginOpenSSLCipherSuites: Unknown command.")

        # Get the list of available cipher suites for the given ssl version
        sslClient = SslClient(sslVersion=sslVersion)
        sslClient.set_cipher_list('ALL:COMPLEMENTOFALL')
        cipher_list = sslClient.get_cipher_list()

        # Create a thread pool
        NB_THREADS = min(len(cipher_list), MAX_THREADS) # One thread per cipher
        thread_pool = ThreadPool()

        # Scan for every available cipher suite
        for cipher in cipher_list:
            thread_pool.add_job((self._test_ciphersuite,
                                 (target, sslVersion, cipher)))

        # Scan for the preferred cipher suite
        thread_pool.add_job((self._pref_ciphersuite,
                             (target, sslVersion)))

        # Start processing the jobs
        thread_pool.start(NB_THREADS)

        result_dicts = {'preferredCipherSuite':{}, 'acceptedCipherSuites':{},
                        'rejectedCipherSuites':{}, 'errors':{}}

        # Store the results as they come
        for completed_job in thread_pool.get_result():
            (job, result) = completed_job
            if result is not None:
                (result_type, ssl_cipher, keysize, msg) = result
                (result_dicts[result_type])[ssl_cipher] = (msg, keysize)

        # Store thread pool errors
        for failed_job in thread_pool.get_error():
            (job, exception) = failed_job
            ssl_cipher = str(job[1][2])
            error_msg = str(exception.__class__.__module__) + '.' \
                        + str(exception.__class__.__name__) + ' - ' + str(exception)
            result_dicts['errors'][ssl_cipher] = (error_msg, None)

        thread_pool.join()

        # Generate results
        return PluginBase.PluginResult(self._generate_text_output(result_dicts, command),
                                       self._generate_xml_output(result_dicts, command))


# == INTERNAL FUNCTIONS ==

# FORMATTING FUNCTIONS
    def _generate_text_output(self, resultDicts, sslVersion):

        cipherFormat = '        {0:<32}{1:<35}'.format
        titleFormat =  '      {0:<32} '.format
        keysizeFormat = '{0:<30}{1:<14}'.format

        txtOutput = [self.PLUGIN_TITLE_FORMAT(sslVersion.upper() + ' Cipher Suites')]

        dictTitles = [('preferredCipherSuite', 'Preferred Cipher Suite:'),
                      ('acceptedCipherSuites', 'Accepted Cipher Suite(s):'),
                      ('errors', 'Undefined - An unexpected error happened:'),
                      ('rejectedCipherSuites', 'Rejected Cipher Suite(s):')]

        if self._shared_settings['hide_rejected_ciphers']:
            dictTitles.pop(3)
            txtOutput.append('')
            txtOutput.append(titleFormat('Rejected Cipher Suite(s): Hidden'))

        for (resultKey, resultTitle) in dictTitles:

            # Sort the cipher suites by results
            result_list = sorted(resultDicts[resultKey].iteritems(),
                                 key=lambda (k,v): (v,k), reverse=True)

            # Add a new line and title
            if len(resultDicts[resultKey]) == 0: # No ciphers
                pass # Hide empty results
                # txtOutput.append(titleFormat(resultTitle + ' None'))
            else:
                txtOutput.append('')
                txtOutput.append(titleFormat(resultTitle))

                # Add one line for each ciphers
                for (cipherTxt, (msg, keysize)) in result_list:
                    if keysize:
                        cipherTxt = keysizeFormat(cipherTxt, keysize)

                    txtOutput.append(cipherFormat(cipherTxt, msg))

        return txtOutput


    def _generate_xml_output(self, result_dicts, command):

        xmlOutput = Element(command, title=command.upper() + ' Cipher Suites')

        for (resultKey, resultDict) in result_dicts.items():
            xmlNode = Element(resultKey)

            # Sort the cipher suites by name to make the XML diff-able
            resultList = sorted(resultDict.items(),
                                 key=lambda (k,v): (k,v), reverse=False)

            # Add one element for each ciphers
            for (sslCipher, (msg, keysize)) in resultList:
                cipherXmlAttr = {'name' : sslCipher, 'connectionStatus' : msg}
                if keysize:
                    cipherXmlAttr['keySize'] = keysize
                cipherXml = Element('cipherSuite', attrib = cipherXmlAttr)

                xmlNode.append(cipherXml)

            xmlOutput.append(xmlNode)

        return xmlOutput


# SSL FUNCTIONS
    def _test_ciphersuite(self, target, ssl_version, ssl_cipher):
        """
        Initiates a SSL handshake with the server, using the SSL version and
        cipher suite specified.
        """
        sslConn = create_sslyze_connection(target, self._shared_settings, ssl_version)
        sslConn.set_cipher_list(ssl_cipher)

        try: # Perform the SSL handshake
            sslConn.connect()

        except SSLHandshakeRejected as e:
            return ('rejectedCipherSuites', ssl_cipher, None, str(e))

        except:
            raise

        else:
            ssl_cipher = sslConn.get_cipher_name()
            if 'ADH' in ssl_cipher or 'AECDH' in ssl_cipher:
                keysize = 'Anon' # Anonymous, let s not care about the key size
            else:
                keysize = str(sslConn.get_cipher_bits()) + ' bits'

            status_msg = sslConn.post_handshake_check()
            return ('acceptedCipherSuites', ssl_cipher, keysize, status_msg)

        finally:
            sslConn.close()


    def _pref_ciphersuite(self, target, ssl_version):
        """
        Initiates a SSL handshake with the server, using the SSL version and cipher
        suite specified.
        """
        sslConn = create_sslyze_connection(target, self._shared_settings, ssl_version)

        try: # Perform the SSL handshake
            sslConn.connect()

            ssl_cipher = sslConn.get_cipher_name()
            if 'ADH' in ssl_cipher or 'AECDH' in ssl_cipher:
                keysize = 'Anon' # Anonymous, let s not care about the key size
            else:
                keysize = str(sslConn.get_cipher_bits())+' bits'

            status_msg = sslConn.post_handshake_check()
            return ('preferredCipherSuite', ssl_cipher, keysize, status_msg)

        except:
            return None

        finally:
            sslConn.close()

