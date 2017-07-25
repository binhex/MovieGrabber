# set moviegrabber and db schema version numbers
latest_mg_version = "2.2.1.0"
latest_db_version = "3"

# TODO put in support for deluge magnet links
# TODO test and verify all cli options working
# TODO put in better validation of config.ibi to stop crash on bad entries through webui
# TODO put all globals in functions and return values, use *args for input if required
# TODO at top of download class echo out movie name and url to download and details
# TODO split large search index class into multiple classes
# TODO bug with pref group, picks up hyphen in title, e.g. ant-man thinks groupname is man
# TODO add in api.torrentsapi.com to list of torrent index sites
# TODO add in rarbg to list of torrent index sites

import os
import sys

# if py2exe compiled version
if hasattr(sys, "frozen"):

    # define path to moviegrabber root folder
    moviegrabber_root_dir = os.path.abspath("").decode("utf-8")

else:

    # define path to moviegrabber root path - required for linux
    moviegrabber_root_dir = os.path.dirname(os.path.realpath(__file__)).decode("utf-8")

    python_version = sys.version_info

    # check version of python is 2.6.x or 2.7.x
    if python_version < (2, 6, 0) or (3, 0, 0) <= python_version:

        sys.stderr.write("WARNING - You need Python 2.6.x/2.7.x installed to run MovieGrabber, your running version %s" % (python_version,))
        sys.exit(1)

try:

    import sqlite3

except ImportError:

    sqlite3 = None
    sys.stderr.write("WARNING - Required SQLite Python module missing, please install before running MovieGrabber\n")
    sys.exit(1)

config_dir = os.path.join(moviegrabber_root_dir, u"configs")
config_dir = os.path.normpath(config_dir)

# set paths for configspec.ini
configspec_ini = os.path.join(config_dir, u"configspec.ini")

# -------------------------- shared ------------------------

import urllib
import urllib2
import time
import datetime
import re
import logging
import decimal
import operator
import itertools

# ------------------------- webui ---------------------------

import argparse
import logging.handlers
import shutil
import webbrowser
import threading
import Queue

# ------------------------ search ----------------------------

import socket
import htmlentitydefs
import json
import smtplib
import email.mime.multipart
import email.mime.text
import base64

# -------------------- 3rd party -----------------------------

import requests
import backoff
import configobj
import validate
import xmltodict
import cherrypy
from cherrypy.lib import auth_basic
from dateutil.parser import parse
from Cheetah.Template import Template
from sqlalchemy import create_engine, exc, Column, Integer, String, desc, asc, func, PickleType
from sqlalchemy.orm import sessionmaker, scoped_session
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.sql import text

# required to suppress ssl warning for urllib3 (requests uses urllib3)
import requests.packages.urllib3
requests.packages.urllib3.disable_warnings()

# ---------------------- py2exe -------------------------------

import email.generator
import email.iterators

# -------------------------------------------------------------

# sets timeout period for retrieve (in seconds)
socket.setdefaulttimeout(240)

# user agent strings
user_agent_chrome = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_12_3) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/59.0.3071.115 Safari/537.36"
user_agent_iphone = "Mozilla/5.0 (iPhone; U; CPU iPhone OS 3_0 like Mac OS X; en-us) AppleWebKit/528.18 (KHTML, like Gecko) Version/4.0 Mobile/7A341 Safari/528.16"
user_agent_moviegrabber = "moviegrabber/%s; https://sourceforge.net/projects/moviegrabber" % latest_mg_version

# tools
###


# function to check host has an assigned ip address (if not using local loopback)
def host_ip():

    # get network info for network adapter
    network_info = socket.getaddrinfo(socket.gethostname(), None)

    # get moviegrabber config.ini address
    config_address = config_instance.config_obj["webconfig"]["address"]

    # if moviegrabber config.ini address is local address then skip check
    if config_address != "0.0.0.0" or "127.0.0.1" or "localhost":

        # loop over list of ip's, will work for IPv4 and ipv6
        for item in network_info:

            host_ip_address = str(item[4][0])

            # check host has valid ipv4 address, dhcp lease or static ip
            if '.' in host_ip_address:

                mg_log.info(u"Host has valid ipv4 address \"%s\"" % host_ip_address)
                return 1

        return 0

    else:

        return 1


# function to check ip specified in config.ini exists on host (if not using local loopback)
def config_ip():

    # get network info for network adapter
    network_info = socket.getaddrinfo(socket.gethostname(), None)

    # get moviegrabber config.ini address
    config_address = config_instance.config_obj["webconfig"]["address"]

    # if moviegrabber config.ini address is local address then skip check
    if config_address != ("0.0.0.0" or "127.0.0.1" or "localhost"):

        # loop over list of ip's, will work for ipv4 and ipv6
        for item in network_info:

            # if config.ini entry is blank, break loop and reset
            if config_address == "":

                break

            host_ip_address = str(item[4][0])

            # check config.ini ipv4 address exists on host
            if config_address in host_ip_address:

                mg_log.info(u"Config has valid ipv4 address \"%s\"" % host_ip_address)
                return

        # if address not valid then set to default listen on all adapters
        config_instance.config_obj["webconfig"]["address"] = "0.0.0.0"

        # write settings to config.ini
        config_instance.config_obj.write()

        mg_log.info(u"Config has invalid ipv4 address \"%s\", reset to 0.0.0.0" % config_address)

    else:

        mg_log.info(u"Config has valid loopback/listen on all addresses \"%s\"" % config_address)


# function to find out external ip address
def external_ip(*site_url_list):

    for site_url_item in site_url_list:

        # download external ip in json format
        status_code, content = metadata_download(site_url_item, user_agent_iphone)

        if status_code != 200:

            mg_log.warning(u"Cannot download external IP address info from %s" % site_url_item)
            continue

        else:

            try:

                # download external ip in json format
                external_ip_json_page = json.loads(content)

                if "jsonip" in site_url_item:

                    external_ip_address = external_ip_json_page["ip"]
                    return external_ip_address

                elif "ifconfig" in site_url_item:

                    external_ip_address = external_ip_json_page["ip_addr"]
                    return external_ip_address

            except (ValueError, TypeError, KeyError):

                mg_log.warning(u"JSON format error for external IP address info from %s" % site_url_item)
                continue

    return 1


# function to remove comma's, periods and spaces from begining and end of strings
def del_inv_chars(text_input):

    text_output = re.sub(ur"[\s,\.]+$", "", text_input)
    text_output = re.sub(ur"^[\s,\.]+", "", text_output)

    return text_output


# debug for text type
def string_type(name):

    # prints out whether string, unicode or other
    if isinstance(name, str):

        print "byte string"

    elif isinstance(name, unicode):

        print "unicode string"

    else:

        print "not string"


# used to decode byte strings to unicode, either utf-8 (normally used on linux) or cp1252 (windows)
def byte_to_uni(name):

    # if type is byte string then decode to unicode, otherwise assume already unicode
    if isinstance(name, str) and name is not None:

        try:

            # linux default encode
            name = name.decode('utf8')

        except UnicodeDecodeError:

            # windows default encode
            name = name.decode('windows-1252')

    return name


# used to encode unicode to byte strings, either utf-8 (normally used on linux) or cp1252 (windows)
def uni_to_byte(name):

    # if type is unicode then encode to byte string, otherwise assume already byte string
    if isinstance(name, unicode) and name is not None:

        try:

            # linux default encode
            name = name.encode('utf8')

        except UnicodeEncodeError:

            # windows default encode
            name = name.encode('windows-1252')

    return name


@backoff.on_exception(backoff.expo, (socket.timeout, requests.exceptions.Timeout, requests.exceptions.HTTPError), max_tries=10)
def metadata_download(url, user_agent):

    # add headers for gzip support and custom user agent string
    headers = {
        'Accept-encoding': 'gzip',
        'User-Agent': user_agent,
    }

    # set connection timeout value (max time to wait for connection)
    connect_timeout = 5.0

    # set read timeout value (max time to wait between each byte)
    read_timeout = 10.0

    # use a session instance to customize how "requests" handles making http requests
    session = requests.Session()

    # set status_code and content to None incase nothing returned
    status_code = None
    content = None

    try:

        # request url get with timeouts and custom headers
        response = session.get(url=url, timeout=(connect_timeout, read_timeout), headers=headers, allow_redirects=True, verify=False)

        # get status code and content downloaded
        status_code = response.status_code

        # if status code is not 200 and not 404 (file not found) then raise exception to cause backoff
        if status_code == 200:

            mg_log.info(u"Status code %s, download succeeded for %s" % (status_code, url))
            content = response.content

        elif status_code != 404:

            mg_log.warning(u"Status code %s != 200, download failed for %s" % (status_code, url))
            raise requests.exceptions.HTTPError

    except requests.exceptions.ConnectTimeout:

        # connect timeout occurred
        mg_log.warning(u"Index site feed/api download connect timeout for %s" % url)

    except requests.exceptions.ConnectionError as e:

        # connection error occurred
        mg_log.warning(u"Index site feed/api download connection error %s for %s" % (e, url))

    except requests.exceptions.TooManyRedirects:

        # too many redirects, bad site or circular redirect
        mg_log.warning(u"Index site feed/api download too many redirects for %s" % url)

    except requests.exceptions.RequestException:

        # catch any other exceptions thrown by requests
        mg_log.warning(u"Index site feed/api download failed, giving up for %s" % url)

    return status_code, content


def decode_html_entities(text_input):

    # search for entity value to replace
    entity = re.compile(ur"&# ?\w+;", re.IGNORECASE).search(text_input)

    if entity is not None:

        entity = entity.group()

        if entity[:2] == "&# ":

            try:

                # hex character reference
                if entity[:3] == "&# x":

                    fixed_text = unichr(int(entity[3:-1], 16))

                else:

                    fixed_text = unichr(int(entity[2:-1]))

            except ValueError:

                return text_input

        else:

            # named character reference
            try:

                fixed_text = unichr(htmlentitydefs.name2codepoint[entity[1:-1]])

            except KeyError:

                return text_input

        # return replaced text with substituted entity
        return re.sub(ur"&# ?\w+;", fixed_text, text_input)

    else:

        return text_input


class Config(object):

    def __init__(self):

        self.config_ini = None
        self.config_dir = None
        self.certs_dir = None
        self.logs_dir = None
        self.results_dir = None
        self.webconfig_address = None
        self.webconfig_port = None

    def config_cli(self):

        # if lib folder exists (not compiled windows binary) then enable argparse (py2exe doesnt allow arguments)
        if uni_to_byte(os.path.exists(os.path.join(moviegrabber_root_dir, u"lib"))):

            # custom argparse to redirect user to help if unknown argument specified
            class ArgparseCustom(argparse.ArgumentParser):

                def error(self, message):

                    sys.stderr.write('error: %s\n' % message)
                    self.print_help()
                    sys.exit(2)

            # setup argparse description and usage, also increase spacing for help to 50
            commandline_parser = ArgparseCustom(prog="MovieGrabber", description="%(prog)s " + latest_mg_version, usage="%(prog)s [--help] [--ip <ipaddress>] [--port <portnumber>] [--config <path>] [--certs <path>] [--logs <path>] [--db <path>] [--pidfile <path>] [--daemon] [--reset] [--version]", formatter_class=lambda prog: argparse.HelpFormatter(prog, max_help_position=50))

            # add argparse command line flags
            commandline_parser.add_argument(u"--ip", metavar=u"<ipaddress>", help=u"specify ip e.g. --ip 192.168.1.2")
            commandline_parser.add_argument(u"--port", metavar=u"<port>", help=u"specify port e.g. --port 9191")
            commandline_parser.add_argument(u"--config", metavar=u"<path>", help=u"specify path for config file e.g. --config /opt/moviegrabber/config/")
            commandline_parser.add_argument(u"--certs", metavar=u"<path>", help=u"specify path for ssl cert files e.g. --certs /opt/moviegrabber/certs/")
            commandline_parser.add_argument(u"--logs", metavar=u"<path>", help=u"specify path for log files e.g. --logs /opt/moviegrabber/logs/")
            commandline_parser.add_argument(u"--db", metavar=u"<path>", help=u"specify path for database e.g. --db /opt/moviegrabber/db/")
            commandline_parser.add_argument(u"--pidfile", metavar=u"<path>", help=u"specify path to pidfile e.g. --pid /var/run/moviegrabber/moviegrabber.pid")
            commandline_parser.add_argument(u"--daemon", action=u"store_true", help=u"run as daemonized process")
            commandline_parser.add_argument(u"--reset-config", action=u"store_true", help=u"reset config to default")
            commandline_parser.add_argument(u"--reset-db", action=u"store_true", help=u"reset database to default")
            commandline_parser.add_argument(u"--version", action=u"version", version=latest_mg_version)

            # save arguments in dictionary
            args = vars(commandline_parser.parse_args())

            # if argument specified then use
            if args["config"] is not None:

                if not uni_to_byte(os.path.exists(args["config"])):

                    try:

                        # create path recursively
                        os.makedirs(args["config"])
                        self.config_dir = os.path.normpath(args["config"])

                        # create full path to config.ini
                        self.config_ini = os.path.join(self.config_dir, u"config.ini")
                        self.config_ini = os.path.normpath(self.config_ini)

                    except WindowsError:

                        # if cannot create then use default
                        self.config_dir = os.path.join(moviegrabber_root_dir, u"configs")
                        self.config_dir = os.path.normpath(self.config_dir)

                else:

                    self.config_dir = os.path.normpath(args["config"])

                    # create full path to config.ini
                    self.config_ini = os.path.join(self.config_dir, u"config.ini")
                    self.config_ini = os.path.normpath(self.config_ini)

            else:

                # if not defined then use default
                self.config_dir = os.path.join(moviegrabber_root_dir, u"configs")
                self.config_dir = os.path.normpath(self.config_dir)

                # create full path to config.ini
                self.config_ini = os.path.join(self.config_dir, u"config.ini")
                self.config_ini = os.path.normpath(self.config_ini)

            # create configobj instance, set config.ini file, set encoding and set configspec.ini file
            self.config_obj = configobj.ConfigObj(self.config_ini, list_values=False, write_empty_values=True, encoding='UTF-8', default_encoding='UTF-8', configspec=configspec_ini)

            # if argument specified then use
            if args["certs"] is not None:

                if not uni_to_byte(os.path.exists(args["certs"])):

                    try:

                        # create path recursively
                        os.makedirs(args["certs"])
                        self.certs_dir = os.path.normpath(args["certs"])

                    except WindowsError:

                        # if cannot create then use default
                        self.certs_dir = os.path.join(moviegrabber_root_dir, u"certs")
                        self.certs_dir = os.path.normpath(self.certs_dir)

                else:

                    self.certs_dir = os.path.normpath(args["certs"])

            # if argument specified then use
            if args["logs"] is not None:

                if not uni_to_byte(os.path.exists(args["logs"])):

                    try:

                        # create path recursively
                        os.makedirs(args["logs"])
                        self.logs_dir = os.path.normpath(args["logs"])

                    except WindowsError:

                        # if cannot create then use default
                        self.logs_dir = os.path.join(moviegrabber_root_dir, u"logs")
                        self.logs_dir = os.path.normpath(self.logs_dir)

                else:

                    self.logs_dir = os.path.normpath(args["logs"])

            # if argument specified then use
            if args["db"] is not None:

                if not uni_to_byte(os.path.exists(args["db"])):

                    try:

                        # create path recursively
                        os.makedirs(args["db"])
                        self.results_dir = os.path.normpath(args["db"])

                    except WindowsError:

                        # if cannot create then use default
                        self.results_dir = os.path.join(moviegrabber_root_dir, u"db")
                        self.results_dir = os.path.normpath(self.results_dir)

                else:

                    self.results_dir = os.path.normpath(args["db"])

            # if argument specified then use
            if args["ip"] is not None:

                self.webconfig_address = args["ip"]

            # if argument specified then use
            if args["port"] is not None:

                self.webconfig_port = args["port"]

            # check os is not windows and then create pidfile for cherrypy forked process
            if args["pidfile"] is not None and os.name != "nt":

                # create pidfile for daemonized process, used to end process in unraid
                pidfile = cherrypy.process.plugins.PIDFile(cherrypy.engine, args["pidfile"])
                pidfile.subscribe()

            # check os is not windows and then run cherrypy as daemonized process
            if args["daemon"] is True and os.name != "nt":

                # run cherrypy as daemonized process
                daemon = cherrypy.process.plugins.Daemonizer(cherrypy.engine)
                daemon.subscribe()

            # if reset flagged then delete existing config.ini
            if args["reset_config"] is True and uni_to_byte(os.path.exists(self.config_ini)):

                os.remove(self.config_ini)

            # if reset flagged then delete existing results.db
            if args["reset_db"] is True and uni_to_byte(os.path.exists(results_db)):

                os.remove(results_db)

    # read defined values to config.ini, if not defined then set to None
    def config_read(self):

        # if not defined via cli then read existing config.ini entry
        if self.certs_dir is not None:

            # read values from config.ini, if key doesnt exist then assume blank config.ini
            try:

                self.certs_dir = self.config_obj["folders"]["certs_dir"]

            except KeyError:

                self.certs_dir = None

        # if not defined via cli then read existing config.ini entry
        if self.logs_dir is not None:

            try:

                self.logs_dir = self.config_obj["folders"]["logs_dir"]

            except KeyError:

                self.logs_dir = None

        # if not defined via cli then read existing config.ini entry
        if self.results_dir is not None:

            try:

                self.results_dir = self.config_obj["folders"]["results_dir"]

            except KeyError:

                self.results_dir = None

        # if not defined via cli then read existing config.ini entry
        if self.webconfig_address is not None:

            try:

                self.webconfig_address = self.config_obj["webconfig"]["address"]

            except KeyError:

                self.webconfig_address = None

        # if not defined via cli then read existing config.ini entry
        if self.webconfig_port is not None:

            try:

                self.webconfig_port = self.config_obj["webconfig"]["port"]

            except KeyError:

                self.webconfig_port = None

    # write out argument defined values to config.ini, if not defined then write defaults
    def config_write(self):

        # check if defined and valid
        if self.config_dir is None or not os.path.exists(self.config_dir):

            self.config_dir = os.path.join(moviegrabber_root_dir, u"configs")
            self.config_obj["folders"]["config_dir"] = self.config_dir

        else:

            self.config_obj["folders"]["config_dir"] = self.config_dir

        # check if defined and valid
        if self.certs_dir is None or not os.path.exists(self.certs_dir):

            self.certs_dir = os.path.join(moviegrabber_root_dir, u"certs")
            self.config_obj["folders"]["certs_dir"] = self.certs_dir

        else:

            self.config_obj["folders"]["certs_dir"] = self.certs_dir

        # check if defined and valid
        if self.logs_dir is None or not os.path.exists(self.logs_dir):

            self.logs_dir = os.path.join(moviegrabber_root_dir, u"logs")
            self.config_obj["folders"]["logs_dir"] = self.logs_dir

        else:

            self.config_obj["folders"]["logs_dir"] = self.logs_dir

        # check if defined and valid
        if self.results_dir is None or not os.path.exists(self.results_dir):

            self.results_dir = os.path.join(moviegrabber_root_dir, u"db")
            self.config_obj["folders"]["results_dir"] = self.results_dir

        else:

            self.config_obj["folders"]["results_dir"] = self.results_dir

        # check if defined
        if self.webconfig_address is None:

            self.webconfig_address = u"0.0.0.0"
            self.config_obj["webconfig"]["address"] = self.webconfig_address

        else:

            self.config_obj["webconfig"]["address"] = self.webconfig_address

        # if not defined via webui and not defined via argument then set to default path
        if self.webconfig_port is None:

            self.webconfig_port = u"9191"
            self.config_obj["webconfig"]["port"] = self.webconfig_port

        else:

            self.config_obj["webconfig"]["port"] = self.webconfig_port

        # set local version
        self.config_obj["general"]["local_version"] = latest_mg_version

        # write out changes
        self.config_obj.write()

    def config_validate(self):

        # create validator instance
        val = validate.Validator()

        # pass validator to configobj instance, copy required to write missing values out to config.ini
        val_result = self.config_obj.validate(val, copy=True, preserve_errors=True)

        # loop over validator and fix any validation failures
        if not val_result:

            for (section_list, key, _) in configobj.flatten_errors(self.config_obj, val_result):

                if key is not None:

                    print self.config_obj.restore_defaults
                    # convert section list to str
                    section_item = ', '.join(section_list)

                    # get value from section and key
                    config_bad_value = self.config_obj[section_item][key]
                    print config_bad_value
                    self.config_obj.restore_default(key)

                    # if the bad value is list then convert
                    if type(config_bad_value) is list:

                        # convert list to comma seperated string
                        config_bad_value_str = ','.join(config_bad_value)

                        # write new string value to config.ini
                        self.config_obj[section_item][key] = config_bad_value_str

                    sys.stdout.write("The '%s' key in the section '%s' failed validation" % (key, ', '.join(section_list)))


# create Config class instance
config_instance = Config()

# run methods to read cli, read config.ini, validate entries and write out
config_instance.config_cli()
config_instance.config_read()
config_instance.config_validate()
config_instance.config_write()

# construct full path to files
cherrypy_log = os.path.join(config_instance.logs_dir, u"cherrypy.log")
cherrypy_access_log = os.path.join(config_instance.logs_dir, u"cherrypy_access.log")
cherrypy_error_log = os.path.join(config_instance.logs_dir, u"cherrypy_error.log")
moviegrabber_log = os.path.join(config_instance.logs_dir, u"moviegrabber.log")
backoff_log = os.path.join(config_instance.logs_dir, u"backoff.log")
sqlite_log = os.path.join(config_instance.logs_dir, u"sqlite.log")
results_db = os.path.join(config_instance.results_dir, "results.db")

# create connection to sqlite db using sqlalchemy
engine = create_engine("sqlite:///%s" % results_db, echo=False)
Base = declarative_base()

# create sqlite session
Session = sessionmaker(bind=engine)

# scoped_session auto generates thread safe local variable (requires remove for each method)
sql_session = scoped_session(Session)


# define tables and columns for history table
class ResultsDBHistory(Base):

    __tablename__ = "history"

    id = Column(Integer, primary_key=True)
    imdbposter = Column(String)
    imdblink = Column(String)
    imdbplot = Column(String)
    imdbdirectors = Column(String)
    imdbwriters = Column(String)
    imdbactors = Column(String)
    imdbcharacters = Column(String)
    imdbgenre = Column(String)
    imdbname = Column(String)
    imdbyear = Column(Integer)
    imdbruntime = Column(Integer)
    imdbrating = Column(String)
    imdbvotes = Column(Integer)
    imdbcert = Column(String)
    postdate = Column(String)
    postdatesort = Column(Integer)
    postsize = Column(String)
    postsizesort = Column(Integer)
    postnfo = Column(String)
    postdetails = Column(String)
    postname = Column(String)
    postnamestrip = Column(String, unique=True)
    postdl = Column(PickleType)
    dlstatus = Column(String)
    dlname = Column(String)
    dltype = Column(String)
    procresult = Column(PickleType)
    procdate = Column(String)
    procdatesort = Column(Integer)

    def __init__(self, imdbposter, imdblink, imdbplot, imdbdirectors, imdbwriters, imdbactors, imdbcharacters, imdbgenre, imdbname, imdbyear, imdbruntime, imdbrating, imdbvotes, imdbcert, postdate, postdatesort, postsize, postsizesort, postnfo, postdetails, postname, postnamestrip, postdl, dlstatus, dlname, dltype, procresult, procdate, procdatesort):

        self.imdbposter = imdbposter
        self.imdblink = imdblink
        self.imdbplot = imdbplot
        self.imdbdirectors = imdbdirectors
        self.imdbwriters = imdbwriters
        self.imdbactors = imdbactors
        self.imdbcharacters = imdbcharacters
        self.imdbgenre = imdbgenre
        self.imdbname = imdbname
        self.imdbyear = imdbyear
        self.imdbruntime = imdbruntime
        self.imdbrating = imdbrating
        self.imdbvotes = imdbvotes
        self.imdbcert = imdbcert
        self.postdate = postdate
        self.postdatesort = postdatesort
        self.postsize = postsize
        self.postsizesort = postsizesort
        self.postnfo = postnfo
        self.postdetails = postdetails
        self.postname = postname
        self.postnamestrip = postnamestrip
        self.postdl = postdl
        self.dlstatus = dlstatus
        self.dlname = dlname
        self.dltype = dltype
        self.procresult = procresult
        self.procdate = procdate
        self.procdatesort = procdatesort


# define tables and columns for queued table
class ResultsDBQueued(Base):

    __tablename__ = "queued"

    id = Column(Integer, primary_key=True)
    imdbposter = Column(String)
    imdblink = Column(String)
    imdbplot = Column(String)
    imdbdirectors = Column(String)
    imdbwriters = Column(String)
    imdbactors = Column(String)
    imdbcharacters = Column(String)
    imdbgenre = Column(String)
    imdbname = Column(String)
    imdbyear = Column(Integer)
    imdbruntime = Column(Integer)
    imdbrating = Column(String)
    imdbvotes = Column(Integer)
    imdbcert = Column(String)
    postdate = Column(String)
    postdatesort = Column(Integer)
    postsize = Column(String)
    postsizesort = Column(Integer)
    postnfo = Column(String)
    postdetails = Column(String)
    postname = Column(String)
    postnamestrip = Column(String, unique=True)
    postdl = Column(PickleType)
    dlstatus = Column(String)
    dlname = Column(String)
    dltype = Column(String)
    procresult = Column(PickleType)
    procdate = Column(String)
    procdatesort = Column(Integer)

    def __init__(self, imdbposter, imdblink, imdbplot, imdbdirectors, imdbwriters, imdbactors, imdbcharacters, imdbgenre, imdbname, imdbyear, imdbruntime, imdbrating, imdbvotes, imdbcert, postdate, postdatesort, postsize, postsizesort, postnfo, postdetails, postname, postnamestrip, postdl, dlstatus, dlname, dltype, procresult, procdate, procdatesort):

        self.imdbposter = imdbposter
        self.imdblink = imdblink
        self.imdbplot = imdbplot
        self.imdbdirectors = imdbdirectors
        self.imdbwriters = imdbwriters
        self.imdbactors = imdbactors
        self.imdbcharacters = imdbcharacters
        self.imdbgenre = imdbgenre
        self.imdbname = imdbname
        self.imdbyear = imdbyear
        self.imdbruntime = imdbruntime
        self.imdbrating = imdbrating
        self.imdbvotes = imdbvotes
        self.imdbcert = imdbcert
        self.postdate = postdate
        self.postdatesort = postdatesort
        self.postsize = postsize
        self.postsizesort = postsizesort
        self.postnfo = postnfo
        self.postdetails = postdetails
        self.postname = postname
        self.postnamestrip = postnamestrip
        self.postdl = postdl
        self.dlstatus = dlstatus
        self.dlname = dlname
        self.dltype = dltype
        self.procresult = procresult
        self.procdate = procdate
        self.procdatesort = procdatesort


# logging
###


def cherrypy_logging():

    # define cherrpy app log
    log = cherrypy.log

    # remove error and access file, specified in rotatingfilehandler
    log.access_file = ""
    log.error_file = ""

    # error log

    # add the log message handler to the logger
    cherrypy_error_rotatingfilehandler = logging.handlers.RotatingFileHandler(cherrypy_log, 'a', maxBytes=10485760, backupCount=3, encoding="utf-8")

    # set logging level to debug
    cherrypy_error_rotatingfilehandler.setLevel(logging.DEBUG)

    # set formatting for app log
    # noinspection PyProtectedMember
    cherrypy_error_rotatingfilehandler.setFormatter(cherrypy._cplogging.logfmt)

    # add RotatingFileHandler for app log
    log.error_log.addHandler(cherrypy_error_rotatingfilehandler)

    # access log

    # add the access message handler to the logger
    cherrypy_access_rotatingfilehandler = logging.handlers.RotatingFileHandler(cherrypy_access_log, 'a', maxBytes=10485760, backupCount=3, encoding="utf-8")

    # set logging level to debug
    cherrypy_access_rotatingfilehandler.setLevel(logging.DEBUG)

    # set formatting for access log
    # noinspection PyProtectedMember
    cherrypy_access_rotatingfilehandler.setFormatter(cherrypy._cplogging.logfmt)

    # add RotatingFileHandler for access log
    log.access_log.addHandler(cherrypy_access_rotatingfilehandler)


def moviegrabber_logging():

    # read log levels
    log_level = config_instance.config_obj["general"]["log_level"]

    # setup formatting for log messages
    moviegrabber_formatter = logging.Formatter("%(asctime)s %(levelname)s %(threadName)s %(module)s %(funcName)s :: %(message)s")

    # setup logger for moviegrabber
    moviegrabber_logger = logging.getLogger("moviegrabber")

    # add rotating log handler
    moviegrabber_rotatingfilehandler = logging.handlers.RotatingFileHandler(moviegrabber_log, "a", maxBytes=10485760, backupCount=3, encoding="utf-8")

    # set formatter for moviegrabber
    moviegrabber_rotatingfilehandler.setFormatter(moviegrabber_formatter)

    # add the log message handler to the logger
    moviegrabber_logger.addHandler(moviegrabber_rotatingfilehandler)

    # set level of logging from config
    if log_level == "INFO":

        moviegrabber_logger.setLevel(logging.INFO)

    elif log_level == "WARNING":

        moviegrabber_logger.setLevel(logging.WARNING)

    elif log_level == "exception":

        moviegrabber_logger.setLevel(logging.ERROR)

    # setup logging to console
    console_streamhandler = logging.StreamHandler()

    # set formatter for console
    console_streamhandler.setFormatter(moviegrabber_formatter)

    # add handler for formatter to the console
    moviegrabber_logger.addHandler(console_streamhandler)

    # set level of logging from config
    if log_level == "INFO":

        console_streamhandler.setLevel(logging.INFO)

    elif log_level == "WARNING":

        console_streamhandler.setLevel(logging.WARNING)

    elif log_level == "exception":

        console_streamhandler.setLevel(logging.ERROR)

    return moviegrabber_logger


def sqlite_logging():

    # read log levels
    log_level = config_instance.config_obj["general"]["log_level"]

    # setup formatting for log messages
    sqlite_formatter = logging.Formatter("%(asctime)s %(levelname)s %(threadName)s :: %(message)s")

    # setup logger for sqlite using sqlalchemy
    sqlite_logger = logging.getLogger("sqlalchemy.engine")

    # add rotating log handler
    sqlite_rotatingfilehandler = logging.handlers.RotatingFileHandler(sqlite_log, "a", maxBytes=10485760, backupCount=3, encoding="utf-8")

    # set formatter for sqlite
    sqlite_rotatingfilehandler.setFormatter(sqlite_formatter)

    # add handler for formatter to the file logger
    sqlite_logger.addHandler(sqlite_rotatingfilehandler)

    # set level of logging from config
    if log_level == "INFO":

        sqlite_logger.setLevel(logging.INFO)

    elif log_level == "WARNING":

        sqlite_logger.setLevel(logging.WARNING)

    elif log_level == "exception":

        sqlite_logger.setLevel(logging.ERROR)

    return sqlite_logger


def backoff_logging():

    # read log levels
    log_level = config_instance.config_obj["general"]["log_level"]

    # setup formatting for log messages
    backoff_formatter = logging.Formatter("%(asctime)s %(levelname)s %(threadName)s %(module)s %(funcName)s :: %(message)s")

    # setup logger for backoff module
    backoff_logger = logging.getLogger("backoff")

    # setup logging to console
    console_streamhandler = logging.StreamHandler()

    # set formatter for console
    console_streamhandler.setFormatter(backoff_formatter)

    # add handler for formatter to the console
    backoff_logger.addHandler(console_streamhandler)

    # set level of logging from config
    if log_level == "INFO":

        console_streamhandler.setLevel(logging.INFO)

    elif log_level == "WARNING":

        console_streamhandler.setLevel(logging.WARNING)

    elif log_level == "exception":

        console_streamhandler.setLevel(logging.ERROR)

    return backoff_logger


# store the logger instances
mg_log = moviegrabber_logging()
sql_log = sqlite_logging()
dl_log = backoff_logging()
cherrypy_logging()

# sqlite check
###


def sqlite_check():

    """notes - The ALTER TABLE command in SQLite allows the user to rename a table or to add a new column to an existing table.
    It is not possible to rename a column, remove a column, or add or remove constraints from a table."""

    # if db file doesnt exist then create from orm
    if not uni_to_byte(os.path.exists(results_db)):

        try:

            mg_log.info(u"database doesnt exist, creating...")

            # create table and column structure from sqlalchemy metadata
            Base.metadata.create_all(engine)

            # set db to latest db version
            sql_session.execute("PRAGMA user_version = %s" % latest_db_version)

            sql_session.execute("VACUUM")

            mg_log.info(u"database created with ver %s" % latest_db_version)

        # capture any sqlalchemy errors and log failure
        except exc.SQLAlchemyError, e:

            mg_log.info(u"database creation failed with error %s" % e)

    else:

        pragma_user_version = sql_session.execute(text("PRAGMA user_version;"))
        current_db_version = pragma_user_version.fetchone()[0]

        # if already up to date then log and exit
        if str(current_db_version) == latest_db_version:

            mg_log.info(u"database up to date, running ver %s" % current_db_version)

        # if current version is greater than latest version then log and exit
        elif str(current_db_version) > latest_db_version:

            mg_log.warning(u"current database version %s is greater than latest version %s, please delete db" % (current_db_version, latest_db_version))
            sys.exit(1)

        # if user version 0 or 1 then upgrade history and queued tables (add constraints and column postnamestrip)
        elif current_db_version <= 1:

            mg_log.info(u"database requires upgrade to schema...")

            try:

                # rename existing tables, prefixing with old_
                sql_session.execute("ALTER TABLE history RENAME TO old_history;")
                sql_session.execute("ALTER TABLE queued RENAME TO old_queued;")

                # create table and column structure from sqlalchemy orm
                Base.metadata.create_all(engine)

                # commit changes
                sql_session.commit()

                # copy all column data from old table to new table, use NULL for column that doest exist (postnamestrip)
                sql_session.execute("INSERT INTO history(imdbposter,imdblink,imdbplot,imdbdirectors,imdbwriters,imdbactors,imdbcharacters,imdbgenre,imdbname,imdbyear,imdbruntime,imdbrating,imdbvotes,imdbcert,postdate,postdatesort,postsize,postsizesort,postnfo,postdetails,postname,postnamestrip,postdl,dlstatus,dlname,dltype,procresult,procdate,procdatesort) SELECT imdbposter,imdblink,imdbplot,imdbdirectors,imdbwriters,imdbactors,imdbcharacters,imdbgenre,imdbname,imdbyear,imdbruntime,imdbrating,imdbvotes,imdbcert,postdate,postdatesort,postsize,postsizesort,postnfo,postdetails,postname,NULL,postdl,dlstatus,dlname,dltype,procresult,procdate,procdatesort FROM old_history;")
                sql_session.execute("INSERT INTO queued(imdbposter,imdblink,imdbplot,imdbdirectors,imdbwriters,imdbactors,imdbcharacters,imdbgenre,imdbname,imdbyear,imdbruntime,imdbrating,imdbvotes,imdbcert,postdate,postdatesort,postsize,postsizesort,postnfo,postdetails,postname,postnamestrip,postdl,dlstatus,dlname,dltype,procresult,procdate,procdatesort) SELECT imdbposter,imdblink,imdbplot,imdbdirectors,imdbwriters,imdbactors,imdbcharacters,imdbgenre,imdbname,imdbyear,imdbruntime,imdbrating,imdbvotes,imdbcert,postdate,postdatesort,postsize,postsizesort,postnfo,postdetails,postname,NULL,postdl,dlstatus,dlname,dltype,procresult,procdate,procdatesort FROM old_queued;")

                # drop old tables if they exist
                sql_session.execute("DROP TABLE IF EXISTS old_history;")
                sql_session.execute("DROP TABLE IF EXISTS old_queued;")

                # set db to current+1 db version
                sql_session.execute("PRAGMA user_version = 2")

                sql_session.execute("VACUUM")

                mg_log.info(u"database upgraded from ver <=1 to ver 2 succeeded")

            # capture any sqlalchemy errors and log failure
            except exc.SQLAlchemyError, e:

                # catch any sqlalchemy error and rollback transaction
                sql_session.rollback()

                mg_log.warning(u"database upgrade from ver <=1 to ver 2 failed with error %s" % (e,))

        # if user version 2 then upgrade history and queued tables (change postdl to pickle and remove column dltype)
        elif current_db_version == 2:

            mg_log.info(u"database requires upgrade to schema...")

            try:

                # rename existing tables, prefixing with "old_"
                sql_session.execute("ALTER TABLE history RENAME TO old_history;")
                sql_session.execute("ALTER TABLE queued RENAME TO old_queued;")

                # create table and column structure from sqlalchemy orm
                Base.metadata.create_all(engine)

                # commit changes
                sql_session.commit()

                # copy all column data (excluding dltype) from old table to new table
                sql_session.execute("INSERT INTO history(imdbposter,imdblink,imdbplot,imdbdirectors,imdbwriters,imdbactors,imdbcharacters,imdbgenre,imdbname,imdbyear,imdbruntime,imdbrating,imdbvotes,imdbcert,postdate,postdatesort,postsize,postsizesort,postnfo,postdetails,postname,postnamestrip,postdl,dlstatus,dlname,dltype,procresult,procdate,procdatesort) SELECT imdbposter,imdblink,imdbplot,imdbdirectors,imdbwriters,imdbactors,imdbcharacters,imdbgenre,imdbname,imdbyear,imdbruntime,imdbrating,imdbvotes,imdbcert,postdate,postdatesort,postsize,postsizesort,postnfo,postdetails,postname,postnamestrip,postdl,dlstatus,dlname,dltype,procresult,procdate,procdatesort FROM old_history;")
                sql_session.execute("INSERT INTO queued(imdbposter,imdblink,imdbplot,imdbdirectors,imdbwriters,imdbactors,imdbcharacters,imdbgenre,imdbname,imdbyear,imdbruntime,imdbrating,imdbvotes,imdbcert,postdate,postdatesort,postsize,postsizesort,postnfo,postdetails,postname,postnamestrip,postdl,dlstatus,dlname,dltype,procresult,procdate,procdatesort) SELECT imdbposter,imdblink,imdbplot,imdbdirectors,imdbwriters,imdbactors,imdbcharacters,imdbgenre,imdbname,imdbyear,imdbruntime,imdbrating,imdbvotes,imdbcert,postdate,postdatesort,postsize,postsizesort,postnfo,postdetails,postname,postnamestrip,postdl,dlstatus,dlname,dltype,procresult,procdate,procdatesort FROM old_queued;")

                # drop old tables if they exist
                sql_session.execute("DROP TABLE IF EXISTS old_history;")
                sql_session.execute("DROP TABLE IF EXISTS old_queued;")

                # set db to current+1 db version
                sql_session.execute("PRAGMA user_version = 3")

                sql_session.execute("VACUUM")

                mg_log.info(u"database upgraded from ver 2 to ver 3 succeeded")

            # capture any sqlalchemy errors and log failure
            except exc.SQLAlchemyError, e:

                # catch any sqlalchemy error and rollback transaction
                sql_session.rollback()

                mg_log.warning(u"database upgrade from ver 2 to ver 3 failed with error %s" % (e,))

    # remove scoped session
    sql_session.remove()


class Download(object):

    # create instance variables to pass between download methods
    def __init__(self):

        self.sqlite_row = None
        self.sqlite_id_item = None
        self.sqlite_table = None
        self.download_url_item = None
        self.download_type_item = None
        self.dlstatus_msg = None
        self.download_read_url = None

    def download_read(self):

        # get queue content for automated and queue items (sqlite id), this is in a dictionary
        download_details_queue_contents = download_details_queue.get()
        download_details_queue.task_done()

        # get dictionary value for sqlite_id, id's will be in a list
        sqlite_id_list = download_details_queue_contents.get("sqlite_id")

        for self.sqlite_id_item in sqlite_id_list:

            if not download_poison_queue.empty():

                # send task done and exit function
                download_poison_queue.task_done()
                mg_log.info(u"Shutting down downloader")
                return

            # get dictionary value for self.sqlite_table
            self.sqlite_table = download_details_queue_contents.get("sqlite_table")

            if self.sqlite_table == "history":

                # select row from history table for selected id
                self.sqlite_row = sql_session.query(ResultsDBHistory).filter(ResultsDBHistory.id == self.sqlite_id_item).first()

                # remove scoped session
                sql_session.remove()

                # make sure sqlite row exists in history table for selected id
                if self.sqlite_row is None:

                    continue

            else:

                # select row from queued table for selected id
                self.sqlite_row = sql_session.query(ResultsDBQueued).filter(ResultsDBQueued.id == self.sqlite_id_item).first()

                # remove scoped session
                sql_session.remove()

                # make sure sqlite row exists in queue tables for selected id
                if self.sqlite_row is None:

                    continue

            # iterate over list of items in dict getting download type and url
            for self.download_type_item, self.download_url_item in self.sqlite_row.postdl.iteritems():

                if not download_poison_queue.empty():

                    # send task done and exit function
                    download_poison_queue.task_done()
                    mg_log.info(u"Shutting down downloader")
                    return

                if self.download_type_item == "nzb":

                    # read watch directory entries from config.ini
                    config_watch_dir = config_instance.config_obj["folders"]["usenet_watch_dir"]
                    config_watch_dir = os.path.normpath(config_watch_dir)

                    # if nzb client defined self.download_nzb_client()
                    # if readback from nzb client not succesfult then fallback to blackhole below

                    if config_watch_dir != "":

                        self.download_read_watched()

                    else:

                        return

                if self.download_type_item == "torrent":

                    # read watch directory entries from config.ini
                    config_watch_dir = config_instance.config_obj["folders"]["torrent_watch_dir"]
                    config_watch_dir = os.path.normpath(config_watch_dir)

                    # if torrent client defined self.download_torrent_client()
                    # if readback from nzb client not succesfult then fallback to blackhole below

                    if config_watch_dir != "":

                        self.download_read_watched()

                    else:

                        return

                if self.download_type_item == "magnet":

                    pass
                    # if torrent client defined self.download_torrent_client() else return

    def download_torrent_client(self):

        pass

    def download_nzb_client(self):

        pass

    def download_read_watched(self):

        # this reads the nzb/torrent file from the download link
        status_code, content = metadata_download(self.download_url_item, user_agent_moviegrabber)

        if status_code != 200:

            # set result to downloaded for history/queue status
            self.dlstatus_msg = "Failed"

            # run function to write status
            self.download_status()

            mg_log.warning(u"Failed to download metadata from Index Site")
            return

        # run download write method to blackhole
        self.download_write_watched(content)

    def download_write_watched(self, content):

        # check if download type is torrent or nzb
        if self.download_type_item == "nzb":

            # read watch directory entries from config.ini
            config_watch_dir = config_instance.config_obj["folders"]["usenet_watch_dir"]
            config_watch_dir = os.path.normpath(config_watch_dir)

            download_filename = u"%s.nzb" % self.sqlite_row.postname

        else:

            # read watch directory entries from config.ini
            config_watch_dir = config_instance.config_obj["folders"]["torrent_watch_dir"]
            config_watch_dir = os.path.normpath(config_watch_dir)

            download_filename = u"%s.torrent" % self.sqlite_row.postname

        # check watched folder exists, if not continue
        if uni_to_byte(os.path.exists(config_watch_dir)):

            # construct full path and filename
            download_path_filename = os.path.join(config_watch_dir, download_filename)

            mg_log.info(u"Watched folder exists %s" % config_watch_dir)

        else:

            # set result to downloaded for history/queue status
            self.dlstatus_msg = "Failed"

            # run function to write status
            self.download_status()

            mg_log.info(u"Watched folder does not exist %s" % config_watch_dir)
            return 0

        # if nzb/torrent does exist in watched folder and 0 bytes in size (failed download) or has a ".invalid" extension (invalid torrent download) then delete
        if uni_to_byte(os.path.exists(download_path_filename)) and (os.path.getsize(download_path_filename) == 0 or os.path.splitext(download_path_filename)[-1].lower() == ".invalid"):

            try:

                os.remove(download_path_filename)
                mg_log.info(u"Deleted zero byte file %s" % download_path_filename)

            except OSError:

                # set result to downloaded for history/queue status
                self.dlstatus_msg = "Failed"

                # run function to write status
                self.download_status()

                mg_log.info(u"Cannot delete zero byte file %s" % download_path_filename)
                return

        # check nzb/torrent does not exist in watched folder
        if not uni_to_byte(os.path.exists(download_path_filename)):

            try:

                # write nzb/torrent to file in watched folder
                download_write = open(download_path_filename, "wb")
                download_write.write(content)
                download_write.close()

            except IOError:

                # set result to downloaded for history/queue status
                self.dlstatus_msg = "Failed"

                # run function to write status
                self.download_status()

                mg_log.info(u"Write of nzb/torrent failed, whilst writing to %s" % download_path_filename)
                return

            # set result to downloaded for history/queue status
            self.dlstatus_msg = "Downloaded"

            # run function to write status
            self.download_status()

            mg_log.info(u"Write of nzb/torrent successful for file %s" % download_path_filename)

        else:

            # set result to downloaded for history/queue status
            self.dlstatus_msg = "Failed"

            # run function to write status
            self.download_status()

            mg_log.warning(u"Write of nzb/torrent failed, already exists in watched folder %s" % download_path_filename)
            return 0

        # if item was queued then delete from queued table amd shrink db
        if self.sqlite_table == "queued":

            # delete row
            sql_session.delete(self.sqlite_row)
            sql_session.commit()

            # shrink database
            sql_session.execute("VACUUM")

            # remove scoped session
            sql_session.remove()

            mg_log.info(u"Deleted queued item from database")

        mg_log.info(u"Download successful for movie %s" % self.sqlite_row.dlname)

    def download_status(self):

        if self.sqlite_row.dlstatus is not None:

            # select row from history table for selected id and update dlstatus
            sql_session.query(ResultsDBHistory).filter(ResultsDBHistory.id == self.sqlite_id_item).update({'dlstatus': self.dlstatus_msg})

            # write result to history table
            sql_session.commit()

            # remove scoped session
            sql_session.remove()

# xbmc notification
###


class XBMC(object):

    # create instance variables to pass between xbmc methods
    def __init__(self):

        self.config_xbmc_host = config_instance.config_obj["xbmc"]["xbmc_host"]
        self.config_xbmc_port = config_instance.config_obj["xbmc"]["xbmc_port"]
        self.config_xbmc_username = config_instance.config_obj["xbmc"]["xbmc_username"]
        self.config_xbmc_password = config_instance.config_obj["xbmc"]["xbmc_password"]
        self.config_xbmc_notification = config_instance.config_obj["xbmc"]["xbmc_notification"]
        self.config_xbmc_library_update = config_instance.config_obj["xbmc"]["xbmc_library_update"]
        self.config_enable_xbmc = config_instance.config_obj["switches"]["enable_xbmc"]

    def xbmc_gui_notify(self, imdb_movie_title_strip, imdb_movie_year_str, download_result_str):

        if self.config_enable_xbmc == "yes":

            # split xbmc comma seperated hosts list and loop to send to all xbmc hosts
            config_xbmc_host_list = self.config_xbmc_host.split(",")

            for config_xbmc_host_item in config_xbmc_host_list:

                self.xbmc_url = "http://%s:%s/jsonrpc" % (config_xbmc_host_item, self.config_xbmc_port)

                if self.config_xbmc_notification == "yes":

                    # send gui notification to xbmc using jsonrpc - on add to queue/download
                    self.xbmc_jsonrpc = '{"jsonrpc": "2.0","method": "GUI.ShowNotification","params": {"title":"MovieGrabber","message":"%s (%s) - %s"},"id": "1"}' % (uni_to_byte(imdb_movie_title_strip), imdb_movie_year_str, download_result_str)

                    # send to xbmc request function
                    self.xbmc_send_request()

    def xbmc_library_update(self):

        if self.config_enable_xbmc == "yes":

            # split xbmc comma seperated hosts list and loop to send to all xbmc hosts
            config_xbmc_host_list = self.config_xbmc_host.split(",")

            for config_xbmc_host_item in config_xbmc_host_list:

                self.xbmc_url = "http://%s:%s/jsonrpc" % (config_xbmc_host_item, self.config_xbmc_port)

                if self.config_xbmc_library_update == "yes":

                    # force xbmc library update using jsonrpc - post processing only
                    self.xbmc_jsonrpc = '{"jsonrpc": "2.0", "method": "VideoLibrary.Scan", "id": "1"}'

                    # send to xbmc request function
                    self.xbmc_send_request()

    def xbmc_send_request(self):

        # create request and set content type to json - required
        xbmc_request = urllib2.Request(self.xbmc_url, self.xbmc_jsonrpc, {'Content-Type': 'application/json'})

        # add authorisation header to send xbmc username and password
        base64string = base64.standard_b64encode('%s:%s' % (self.config_xbmc_username, self.config_xbmc_password))
        xbmc_request.add_header("Authorization", "Basic %s" % base64string)

        try:

            # send request to xbmc
            urllib2.urlopen(xbmc_request)
            mg_log.info(ur"XBMC JSONRPC request succeeded for host url %s" % self.xbmc_url)

        except Exception:

            mg_log.warning(u"XBMC JSONRPC request failed for host url %s" % self.xbmc_url)


# search index
###


class SearchIndex(object):

    # create instance variables to pass between search index methods
    def __init__(self, download_method, index_site_item, user_agent):

        # define instance variables from arguments
        self.site_feed = None
        self.download_method = download_method
        self.index_site_item = index_site_item
        self.user_agent = user_agent

        # define instance variables for methods
        self.filter_check_status = 0
        self.filter_imdb_fav_title_result = 0
        self.filter_imdb_fav_char_result = 0
        self.filter_imdb_fav_actor_result = 0
        self.filter_imdb_fav_writer_result = 0
        self.filter_imdb_fav_dir_result = 0
        self.filter_imdb_good_date_result = 0
        self.filter_imdb_good_genre_result = 0
        self.filter_imdb_good_votes_result = 0
        self.filter_imdb_good_ratings_result = 0
        self.filter_index_preferred_group_result = 0
        self.filter_index_special_cut_result = 0
        self.filter_imdb_bad_title_result = 0
        self.filter_os_completed_result = 0
        self.filter_os_archive_result = 0
        self.filter_os_movies_replace_result = 0
        self.filter_os_movies_downloaded_result = 0
        self.filter_os_queued_result = 0
        self.filter_os_watched_result = 0
        self.filter_index_bad_report_result = 0

        self.imdb_movie_cert = None
        self.imdb_movie_genres = None
        self.imdb_movie_genres_str = ""
        self.imdb_movie_description = None
        self.imdb_movie_chars_str = ""
        self.imdb_movie_chars = None
        self.imdb_movie_actors_str = ""
        self.imdb_movie_actors = None
        self.imdb_movie_writers_str = ""
        self.imdb_movie_writers = None
        self.imdb_movie_directors_str = ""
        self.imdb_movie_directors = None
        self.imdb_movie_votes_str = ""
        self.imdb_movie_votes_int = None
        self.imdb_movie_rating_str = ""
        self.imdb_movie_rating_dec = None
        self.imdb_movie_runtime_str = ""
        self.imdb_movie_runtime_int = None
        self.imdb_movie_year_str = ""
        self.imdb_movie_year_int = None
        self.imdb_movie_title = None
        self.imdb_movie_title_year = None
        self.imdb_movie_title_strip = None
        self.imdb_movie_poster = None
        self.site_feed = None
        self.movies_downloaded_filename_list = []

        # read folder paths from config.ini
        self.config_watch_dir = config_instance.config_obj["folders"]["%s_watch_dir" % download_method]
        self.config_completed_dir = config_instance.config_obj["folders"]["%s_completed_dir" % download_method]
        self.config_torrent_archive_dir = config_instance.config_obj["folders"]["torrent_archive_dir"]
        self.config_usenet_archive_dir = config_instance.config_obj["folders"]["usenet_archive_dir"]
        self.config_watch_dir = os.path.normpath(self.config_watch_dir)
        self.config_completed_dir = os.path.normpath(self.config_completed_dir)
        self.config_torrent_archive_dir = os.path.normpath(self.config_torrent_archive_dir)
        self.config_usenet_archive_dir = os.path.normpath(self.config_usenet_archive_dir)

        # read imdb from config.ini
        self.config_bad_title = config_instance.config_obj["imdb"]["bad_title"]
        self.config_fav_title = config_instance.config_obj["imdb"]["fav_title"]
        self.config_fav_char = config_instance.config_obj["imdb"]["fav_char"]
        self.config_fav_actor = config_instance.config_obj["imdb"]["fav_actor"]
        self.config_fav_writer = config_instance.config_obj["imdb"]["fav_writer"]
        self.config_fav_dir = config_instance.config_obj["imdb"]["fav_dir"]
        self.config_queue_genre = config_instance.config_obj["imdb"]["queue_genre"]
        self.config_queue_date_int = int(config_instance.config_obj["imdb"]["queue_date"])
        self.config_good_genre = config_instance.config_obj["imdb"]["good_genre"]
        self.config_good_date_int = int(config_instance.config_obj["imdb"]["good_date"])
        self.config_good_votes_int = int(config_instance.config_obj["imdb"]["good_votes"])
        self.config_good_rating_float = float(config_instance.config_obj["imdb"]["good_rating"])
        self.config_preferred_rating_float = float(config_instance.config_obj["imdb"]["preferred_rating"])
        self.config_preferred_genre = config_instance.config_obj["imdb"]["preferred_genre"]

        # read switches from config.ini
        self.config_enable_append_year = config_instance.config_obj["switches"]["enable_append_year"]
        self.config_enable_email_notify = config_instance.config_obj["switches"]["enable_email_notify"]
        self.config_enable_xbmc = config_instance.config_obj["switches"]["enable_xbmc"]
        self.config_enable_downloaded = config_instance.config_obj["switches"]["enable_downloaded"]
        self.config_enable_replace = config_instance.config_obj["switches"]["enable_replace"]
        self.config_enable_group_filter = config_instance.config_obj["switches"]["enable_group_filter"]
        self.config_enable_preferred = config_instance.config_obj["switches"]["enable_preferred"]
        self.config_enable_favorites = config_instance.config_obj["switches"]["enable_favorites"]
        self.config_enable_queuing = config_instance.config_obj["switches"]["enable_queuing"]
        self.config_enable_email_notify = config_instance.config_obj["switches"]["enable_email_notify"]

        # read search criteria from config.ini
        self.config_search_and = config_instance.config_obj[download_method]["%s_search_and" % index_site_item]
        self.config_search_or = config_instance.config_obj[download_method]["%s_search_or" % index_site_item]
        self.config_search_not = config_instance.config_obj[download_method]["%s_search_not" % index_site_item]
        self.config_cat = config_instance.config_obj[download_method]["%s_cat" % index_site_item]
        self.config_minsize_int = int(config_instance.config_obj[download_method]["%s_minsize" % index_site_item])
        self.config_maxsize_int = int(config_instance.config_obj[download_method]["%s_maxsize" % index_site_item])
        self.config_hostname = config_instance.config_obj[download_method]["%s_hostname" % index_site_item]
        self.config_portnumber = config_instance.config_obj[download_method]["%s_portnumber" % index_site_item]

        # get movies downloaded and movies to replace root directory lists, do not decode leave as byte string for os.walk
        self.config_movies_replace_dir = config_instance.config_obj["folders"]["movies_replace_dir"]
        self.config_movies_replace_dir = os.path.normpath(self.config_movies_replace_dir)
        self.config_movies_downloaded_dir = config_instance.config_obj["folders"]["movies_downloaded_dir"]
        self.config_movies_downloaded_dir = os.path.normpath(self.config_movies_downloaded_dir)

        # read general settings from config.ini
        self.config_movie_title_separator = config_instance.config_obj["general"]["movie_title_separator"]
        self.config_special_cut = config_instance.config_obj["general"]["index_special_cut"]
        self.config_preferred_group = config_instance.config_obj["general"]["index_preferred_group"]
        self.config_bad_group = config_instance.config_obj["general"]["index_bad_group"]
        self.config_bad_report = config_instance.config_obj["general"]["index_bad_report"]
        self.config_posts_to_process_int = int(config_instance.config_obj["general"]["index_posts_to_process"])

        if self.download_method == "usenet":

            # read usenet specific settings from config.ini
            self.config_path = config_instance.config_obj[download_method]["%s_path" % index_site_item]
            self.config_apikey = config_instance.config_obj[download_method]["%s_key" % index_site_item]
            self.config_spotweb_support = config_instance.config_obj[download_method]["%s_spotweb_support" % index_site_item]

        else:
            # read torrent specific settings from config.ini
            self.config_lang = config_instance.config_obj[download_method]["%s_lang" % index_site_item]
            self.config_min_seeds_int = int(config_instance.config_obj["general"]["min_seeds"])
            self.config_min_peers_int = int(config_instance.config_obj["general"]["min_peers"])

        if self.config_movies_downloaded_dir:

            # convert from unicode to byte string for root folders string, used for for os.walk
            self.config_movies_downloaded_dir = uni_to_byte(self.config_movies_downloaded_dir)

            # convert comma seperated string into list - config parser cannot deal with lists
            movies_downloaded_dir_list = self.config_movies_downloaded_dir.split(",")

            try:

                # use itertools to chain multiple root folders and then use os.walk to produce generator output
                self.movies_downloaded_cache = list(itertools.chain.from_iterable(uni_to_byte(os.walk(root_path)) for root_path in movies_downloaded_dir_list))

            except UnicodeDecodeError:

                # if cannot decode non ascii char then log error
                self.movies_downloaded_cache = u""
                mg_log.warning(ur"Cannot decode non ASCII movie titles in Movies Downloaded folder, check locale is set correctly")

        if self.config_movies_replace_dir:

            # convert from unicode to byte string for root folders string, used for for os.walk
            self.config_movies_replace_dir = uni_to_byte(self.config_movies_replace_dir)

            # convert comma seperated string into list - config parser cannot deal with lists
            movies_replace_dir_list = self.config_movies_replace_dir.split(",")

            try:

                # use itertools to chain multiple root folders and then use os.walk to produce generator output
                self.movies_replace_cache = list(itertools.chain.from_iterable(uni_to_byte(os.walk(root_path)) for root_path in movies_replace_dir_list))

            except UnicodeDecodeError:

                # if cannot decode non ascii char then log error
                self.movies_replace_cache = u""
                mg_log.warning(ur"Cannot decode non ASCII movie titles in Movies to Replace folder, check locale is set correctly")

        if self.config_enable_email_notify == "yes":

            # run external_ip() and store return value
            self.external_ip_address = external_ip("http://jsonip.com", "http://ifconfig.me/all.json")

    # os filters
    ###

    def filter_os_queued(self):

        # check if post title is in queued table (case insensitive), if not then proceed
        sqlite_post_name = sql_session.query(ResultsDBQueued).filter(ResultsDBQueued.postname == self.index_post_title).first()

        # if movie post name found in database then return 0
        if sqlite_post_name is not None:

            self.download_details_dict["filter_os_queued_result"] = [0, "Queued", "System - NZB/Torrent is in Queued table"]
            mg_log.info(ur"Filter System - Post title is in Queued table, skip")
            return 0

        else:

            mg_log.info(ur"Filter System - Post title is NOT in Queued table, proceed")
            return 1

    def filter_os_watched(self):

        # this is set to download only if the nzb/torrent file doesn't exist in the watch folder
        if uni_to_byte(os.path.exists(os.path.join(self.config_watch_dir, u"%s.nzb" % self.index_post_title))) or uni_to_byte(os.path.exists(os.path.join(self.config_watch_dir, u"%s.torrent" % self.index_post_title))):

            self.download_details_dict["filter_os_watched_result"] = [0, "Watched", "System - NZB/Torrent is in Watched folder"]
            mg_log.info(ur"Filter System - NZB/Torrent is in Watched folder, skip")
            return 0

        else:

            mg_log.info(ur"Filter System - NZB/Torrent is NOT in Watched folder, proceed")
            return 1

    def filter_os_archive(self):

        # this is set to download only if the nzb/torrent file doesn't exist in the nzb folder
        if uni_to_byte(os.path.exists(os.path.join(self.config_usenet_archive_dir, u"%s.nzb.gz" % self.index_post_title))) or uni_to_byte(os.path.exists(os.path.join(self.config_torrent_archive_dir, u"%s.torrent" % self.index_post_title))):

            self.download_details_dict["filter_os_archive_result"] = [0, "Archive", "System - NZB/Torrent is in Archive folder"]
            mg_log.info(u"Filter System - NZB/Torrent is in Archive folder, skip")
            return 0

        else:

            mg_log.info(u"Filter System - NZB/Torrent is NOT in Archive folder, proceed")
            return 1

    def filter_os_completed(self):

        # this is set to download only if the movie doesn't exist in the completed folder
        if uni_to_byte(os.path.exists(os.path.join(self.config_completed_dir, self.index_post_title))):

            self.download_details_dict["filter_os_completed_result"] = [0, "Completed", "System - NZB/Torrent is in Completed folder"]
            mg_log.info(u"Filter System - IMDb title in Completed Folder, skip")
            return 0

        else:

            mg_log.info(u"Filter System - IMDb title is NOT in Completed folder, proceed")
            return 1

    def filter_os_movies_downloaded(self):

        if self.config_enable_downloaded == "yes" and self.movies_downloaded_cache:

            # escape any regex characters such as brackets for year in title
            imdb_movie_title_year_esc = re.escape(self.imdb_movie_title_year)

            for folder, subs, files in self.movies_downloaded_cache:

                for subdirs in subs:

                    # perform case insensitve match for imdb title with year and custom seperator or imdb title with year and spaces or imdb title with spaces
                    if self.imdb_movie_title.lower() == subdirs.lower() or self.imdb_movie_title_year.lower() == subdirs.lower() or self.imdb_movie_title_strip.lower() == subdirs.lower():

                        # generate list of files with full path in matching downloaded movie folder
                        self.movies_downloaded_filename_list = os.listdir(os.path.join(folder, subdirs))

                        self.download_details_dict["filter_os_movies_downloaded_result"] = [0, "Downloaded", "System - Movie is in Movies Downloaded folder"]
                        mg_log.info(ur"Filter System - IMDb title is in Movies Downloaded folder, skip")
                        return 0

                for filenames in files:

                    # perform partial search for imdb name with year (spaces for seperator) against filename
                    if re.compile(imdb_movie_title_year_esc, re.IGNORECASE).search(filenames):

                        # generate downloaded movie filename, used for preferred group filter
                        self.movies_downloaded_filename_list = [filenames]

                        self.download_details_dict["filter_os_movies_downloaded_result"] = [0, "Downloaded", "System - Movie is in Movies Downloaded folder"]
                        mg_log.info(ur"Filter System - IMDb title is in Movies Downloaded folder, skip")
                        return 0

            mg_log.info(ur"Filter System - IMDb title is NOT in Movies Downloaded folder, proceed")
            return 1

        else:

            return 1

    def filter_os_movies_replace(self):

        if self.config_enable_replace == "yes" and self.movies_replace_cache:

            # escape any regex characters such as brackets for year in title
            imdb_movie_title_year_esc = re.escape(self.imdb_movie_title_year)

            for folder, subs, files in self.movies_replace_cache:

                for subdirs in subs:

                    # perform case insensitve match for imdb title with year and custom seperator or imdb title with year and spaces or imdb title with spaces
                    if self.imdb_movie_title.lower() == subdirs.lower() or self.imdb_movie_title_year.lower() == subdirs.lower() or self.imdb_movie_title_strip.lower() == subdirs.lower():

                        self.download_details_dict["filter_os_movies_replace_result"] = [1, "Replace", "System - Movie is in Movies To Replace folder"]
                        mg_log.info(ur"Filter System - IMDb title is in Movies To Replace folder, proceed")
                        return 1

                for filenames in files:

                    # perform partial regex search for imdb name with year (spaces for seperator) against filename
                    if re.compile(imdb_movie_title_year_esc, re.IGNORECASE).search(filenames):

                        self.download_details_dict["filter_os_movies_replace_result"] = [1, "Replace", "System - Movie is in Movies To Replace folder"]
                        mg_log.info(ur"Filter System - IMDb title is in Movies To Replace folder, proceed")
                        return 1

            mg_log.info(ur"Filter System - IMDb title is NOT in Movies To Replace folder, skip")
            return 0

        else:

            return 0

    # index filters
    ###

    def filter_index_min_seeds(self):

        # if download type not torrent or index min seeds not found or config min seeds not defined then return 1
        if self.config_min_seeds_int == 0:

            mg_log.info(u"Filter Index - Seed count not defined, proceed")
            return 1

        # this is set to download movies with minimum defined seed count
        elif self.index_min_seeds_int >= self.config_min_seeds_int:

            mg_log.info(u"Filter Index - Seed count %s above threshold, proceed" % self.index_min_seeds)
            return 1

        else:

            mg_log.info(u"Filter Index - Seed count %s below threshold, skip" % self.index_min_seeds)
            return 0

    def filter_index_min_peers(self):

        # if download type not torrent or index min peers not found or config min peers not defined then return 1
        if self.config_min_peers_int == 0:

            mg_log.info(u"Filter Index - Peer count not defined, proceed")
            return 1

        # this is set to download movies with minimum defined seed count
        elif self.index_min_peers_int >= self.config_min_peers_int:

            mg_log.info(u"Filter Index - Peer count %s above threshold, proceed" % self.index_min_peers)
            return 1

        else:

            mg_log.info(u"Filter Index - Peer count %s below threshold, skip" % self.index_min_peers)
            return 0

    def filter_index_bad_report(self):

        if self.config_bad_report and self.index_post_id:

            # convert comma seperated string into list and remove spaces from comma seperated values using list comprehension
            config_bad_report_list = [item.strip().lower() for item in self.config_bad_report.split(',')]

            # look for matching item in list
            if self.index_post_id.lower() in config_bad_report_list:

                self.download_details_dict["filter_index_bad_report_result"] = [0, "Bad Report", "Index - Report ID is in Bad list"]
                mg_log.info(u"Filter Index - Report ID %s is in Bad list, skip" % self.index_post_id)
                return 0

            else:

                mg_log.info(u"Filter Index - Report ID %s is NOT in Bad list, proceed" % self.index_post_id)
                return 1

        else:

            return 1

    def filter_index_good_size(self):

        # if index post size cannot be determined (value of 0) then return success
        if self.index_post_size_int == 0:

            mg_log.info(u"Filter Index - Post Size cannot be determined, proceed")
            return 1

        # if min and maxsize not defined then return 1config_enable_group_filter
        if self.config_minsize_int == 0 and self.config_maxsize_int == 0:

            mg_log.info(u"Filter Index - Post Size not defined, proceed")
            return 1

        # if min and maxsize defined then check min and max against post
        elif self.config_minsize_int and self.config_maxsize_int != 0:

            if self.config_minsize_int <= self.index_post_size_int <= self.config_maxsize_int:

                mg_log.info(u"Filter Index - Post Size %s is within thresholds, proceed" % self.index_post_size_int)
                return 1

            else:

                mg_log.info(u"Filter Index - Post Size %s is NOT within thresholds, skip" % self.index_post_size_int)
                return 0

        # if maxsize only defined then check max against post
        elif self.config_minsize_int == 0:

            if self.index_post_size_int <= self.config_maxsize_int:

                mg_log.info(u"Filter Index - Post Size %s is within thresholds, proceed" % self.index_post_size_int)
                return 1

            else:

                mg_log.info(u"Filter Index - Post Size %s is NOT within thresholds, skip" % self.index_post_size_int)
                return 0

        # if minsize only defined then check min against post
        elif self.config_maxsize_int == 0:

            if self.index_post_size_int >= self.config_minsize_int:

                mg_log.info(u"Filter Index - Post Size %s is within thresholds, proceed" % self.index_post_size_int)
                return 1

            else:

                mg_log.info(u"Filter Index - Post Size %s is NOT within thresholds, skip" % self.index_post_size_int)
                return 0

    def filter_index_special_cut(self):

        # check special cut is enabled in switches and movies downloaded return value is zero (movie already downloaded)
        if self.config_special_cut != "" and self.filter_os_movies_downloaded_result == 0:

            # replace comma's with regex OR symbol
            self.config_special_cut = re.sub(ur"[,\s?]+", "|", self.config_special_cut)

            # search for special cut in post title
            index_post_title_special_cut_search = re.compile(ur"(?<=\.|\s)(%s)(?=\.|\s)" % self.config_special_cut, re.IGNORECASE).search(self.index_post_title)

            if index_post_title_special_cut_search is not None:

                index_post_title_special_cut = index_post_title_special_cut_search.group()

                movies_valid_extensions = [".mkv", ".avi", ".mp4", ".dvx", ".wmv", ".mov"]

                # loop over list of files in downloaded folder and check for valid file extensions
                for movies_downloaded_filename_item in self.movies_downloaded_filename_list:

                    # generate filename and file extension
                    movies_downloaded_filename, movies_downloaded_extension = os.path.splitext(movies_downloaded_filename_item.lower())

                    # check movie downloaded filename extension is in valid extensions list
                    if movies_downloaded_extension in movies_valid_extensions:

                        # search for special cut in downloaded filename
                        movies_downloaded_special_cut_search = re.compile(ur"(?<=\.|\s)(%s)(?=\.|\s)" % self.config_special_cut, re.IGNORECASE).search(movies_downloaded_filename)

                        # if search matches then assume we already have downloaded special cut
                        if movies_downloaded_special_cut_search is not None:

                            movies_downloaded_special_cut = movies_downloaded_special_cut_search.group()

                            mg_log.info(u"Filter Index - Filename post title already contains special cut %s, skip" % movies_downloaded_special_cut)
                            return 0

                        else:

                            self.download_details_dict["filter_index_special_cut_result"] = [1, "Special Cut", "Index - Special cut of movie does not exist in downloaded movies folder"]
                            mg_log.info(u"Filter Index - Index post title special cut %s does not exist in filename, force" % index_post_title_special_cut)
                            return 1

        return 0

    def filter_index_preferred_group(self):

        # check preferred group is enabled in switches and movies downloaded return value is zero (movie already downloaded) and config preferred group and index post group are not empty
        if self.config_enable_group_filter == "yes" and self.filter_os_movies_downloaded_result == 0 and self.config_preferred_group and self.index_post_group:

            movies_valid_extensions = [".mkv", ".avi", ".mp4", ".dvx", ".wmv", ".mov"]

            # loop over list of files in downloaded folder and check for valid file extensions
            for movies_downloaded_filename_item in self.movies_downloaded_filename_list:

                # generate filename and file extension
                movies_downloaded_filename, movies_downloaded_extension = os.path.splitext(movies_downloaded_filename_item.lower())

                # check movie downloaded filename extension is in valid extensions list
                if movies_downloaded_extension in movies_valid_extensions:

                    # generate group name from end (first) or start (second) of filename using hyphen as marker
                    movies_downloaded_group_search = re.compile(ur"(?<=-)[^\s\-\.]+$").search(movies_downloaded_filename)

                    if movies_downloaded_group_search is None:

                        movies_downloaded_group_search = re.compile(ur"^[^\s\-\.]+(?=-)").search(movies_downloaded_filename)

                        if movies_downloaded_group_search is None:

                            continue

                    movies_downloaded_group = movies_downloaded_group_search.group()

                    # convert comma seperated string into list and remove spaces from comma seperated values using list comprehension
                    config_preferred_group_list = [x.strip() for x in self.config_preferred_group.split(',')]

                    # use regex to escape characters
                    regex = re.escape(movies_downloaded_group)

                    # look for case insensitive string in list using list comprehension
                    matching_items = [item for item in config_preferred_group_list if re.match(regex.lower(), item.lower())]

                    # if group name for downloaded movie filename is already in preferrred list then return
                    if matching_items:

                        mg_log.info(u"Filter Index - Filename post group %s is in config preferred group list, skip" % movies_downloaded_group)
                        return 0

                    else:

                        # use regex to escape characters
                        regex = re.escape(self.index_post_group)

                        # look for case insensitive string in list using list comprehension
                        matching_items = [item for item in config_preferred_group_list if re.match(regex.lower(), item.lower())]

                        if matching_items:

                            self.download_details_dict["filter_index_preferred_group_result"] = [1, "Preferred Group", "Index - Post group is in Preferred Group list"]
                            mg_log.info(u"Filter Index - Index post group %s is in config preferred group list, force" % self.index_post_group)
                            return 1

                        else:

                            mg_log.info(u"Filter Index - Index post group %s is NOT in config preferred group list, skip" % self.index_post_group)
                            return 0

        return 0

    def filter_index_bad_group(self):

        # check bad group is enabled in switches and config bad group and index post group are not empty
        if self.config_enable_group_filter == "yes" and (self.config_bad_group and self.index_post_group):

            # convert comma seperated string into list and remove spaces from comma seperated values using list comprehension
            config_bad_group_list = [x.strip().lower() for x in self.config_bad_group.split(',')]

            # look for matching item in list
            if self.index_post_group.lower() in config_bad_group_list:

                mg_log.info(u"Filter Index - Index post group %s is in Bad list, skip" % self.index_post_group)
                return 0

            else:

                mg_log.info(u"Filter Index - Index post group %s is NOT in Bad list, proceed" % self.index_post_group)
                return 1

        else:

            return 1

    def filter_index_search_and(self):

        if self.config_search_and:

            # convert comma seperated string into list and remove spaces from comma seperated values using list comprehension
            config_search_and_list = [x.strip() for x in self.config_search_and.split(',')]

            for config_search_and_item in config_search_and_list:

                # use regex word boundary to ensure no partial matching in post title
                config_search_and_item_re = re.compile(ur"\b%s\b" % config_search_and_item, re.IGNORECASE).search(self.index_post_title)

                if config_search_and_item_re:

                    continue

                else:

                    mg_log.info(u"Filter Index - Search criteria MUST exist %s NOT found, skip" % config_search_and_item)
                    return 0

            mg_log.info(u"Filter Index - Search criteria MUST exist found, proceed")
            return 1

        else:

            return 1

    def filter_index_search_or(self):

        if self.config_search_or:

            # convert comma seperated string into list and remove spaces from comma seperated values using list comprehension
            config_search_or_list = [x.strip() for x in self.config_search_or.split(',')]

            for config_search_or_item in config_search_or_list:

                # use regex word boundary to ensure no partial matching in post title
                config_search_or_item_re = re.compile(ur"\b%s\b" % config_search_or_item, re.IGNORECASE).search(self.index_post_title)

                if config_search_or_item_re:

                    mg_log.info(u"Filter Index - Search criteria MAY exist %s found, proceed" % config_search_or_item)
                    return 1

                else:

                    continue

            mg_log.info(u"Filter Index - Search criteria MAY exist NOT found, skip")
            return 0

        else:

            return 1

    def filter_index_search_not(self):

        if self.config_search_not:

            # convert comma seperated string into list and remove spaces from comma seperated values using list comprehension
            config_search_not_list = [x.strip() for x in self.config_search_not.split(',')]

            for config_search_not_item in config_search_not_list:

                # use regex word boundary to ensure no partial matching in post title
                config_search_not_item_re = re.compile(ur"\b%s\b" % config_search_not_item, re.IGNORECASE).search(self.index_post_title)

                if config_search_not_item_re:

                    mg_log.info(u"Filter Index - Search criteria MUST NOT exist %s found, skip" % config_search_not_item)
                    return 0

                else:

                    continue

            mg_log.info(u"Filter Index - Search criteria MUST NOT exist not found, proceed")
            return 1

        else:

            return 1

    # imdb filters
    ###

    def filter_imdb_good_ratings(self):

        # this is set to download movies if preferred genre matches and movie rating is greater than preferred rating
        if self.config_enable_preferred == "yes" and self.filter_imdb_preferred_genre() == 1:

            config_preferred_rating_dec = decimal.Decimal(str(self.config_preferred_rating_float)).quantize(decimal.Decimal('.1'))

            if self.imdb_movie_rating_dec >= config_preferred_rating_dec:

                mg_log.info(u"Filter IMDb - Rating %s above threshold, proceed" % self.imdb_movie_rating_str)
                return 1

            else:

                self.download_details_dict["filter_imdb_good_ratings_result"] = [0, "Rating", "IMDb - Rating below threshold"]
                mg_log.info(u"Filter IMDb - Rating %s below threshold, skip" % self.imdb_movie_rating_str)
                return 0

        else:

            # this is set to download movies if imdb movie rating is greater than config good rating
            config_good_rating_dec = decimal.Decimal(str(self.config_good_rating_float)).quantize(decimal.Decimal('.1'))

            if self.imdb_movie_rating_dec >= config_good_rating_dec:

                mg_log.info(u"Filter IMDb - Rating %s above threshold, proceed" % self.imdb_movie_rating_str)
                return 1

            else:

                self.download_details_dict["filter_imdb_good_ratings_result"] = [0, "Rating", "IMDb - Rating below threshold"]
                mg_log.info(u"Filter IMDb - Rating %s below threshold, skip" % self.imdb_movie_rating_str)
                return 0

    def filter_imdb_good_votes(self):

        # this is set to download movies with minimum defined vote count
        if self.imdb_movie_votes_int >= self.config_good_votes_int:

            mg_log.info(u"Filter IMDb - Votes %s above threshold, proceed" % self.imdb_movie_votes_int)
            return 1

        else:

            self.download_details_dict["filter_imdb_good_votes_result"] = [0, "Votes", "IMDb - Votes below threshold"]
            mg_log.info(u"Filter IMDb - Votes %s below threshold, skip" % self.imdb_movie_votes_int)
            return 0

    def filter_imdb_good_date(self):

        # this is set to download movies with a minimum defined year
        if self.imdb_movie_year_int >= self.config_good_date_int:

            mg_log.info(u"Filter IMDb - Date %s is above threshold, proceed" % self.imdb_movie_year_str)
            return 1

        else:

            self.download_details_dict["filter_imdb_good_date_result"] = [0, "Date", "IMDb - Date is below threshold"]
            mg_log.info(u"Filter IMDb - Date %s is below threshold, skip" % self.imdb_movie_year_str)
            return 0

    def filter_imdb_good_genre(self):

        if self.config_good_genre:

            # convert comma seperated string into list and remove spaces from comma seperated values using list comprehension
            config_good_genre_list = [item.strip().lower() for item in self.config_good_genre.split(',')]

            # convert lists to lowercase using list comprehension
            imdb_movie_genres = [item.lower() for item in self.imdb_movie_genres]

            # use set.intersection to compare items in lists
            matching_items = set(imdb_movie_genres)
            matching_items = matching_items.intersection(config_good_genre_list)

            if set(matching_items):

                mg_log.info(u"Filter IMDb - Genre %s is in Good list, proceed" % self.imdb_movie_genres_str)
                return 1

            else:

                self.download_details_dict["filter_imdb_good_genre_result"] = [0, "Genre", "IMDb - Genre is NOT in Good list"]
                mg_log.info(u"Filter IMDb - Genre %s is NOT in Good list, skip" % self.imdb_movie_genres_str)
                return 0

        else:

            self.download_details_dict["filter_imdb_good_genre_result"] = [0, "Genre", "IMDb - Genre is NOT in Good list"]
            mg_log.info(u"Filter IMDb - Genre %s is NOT in Good list, skip" % self.imdb_movie_genres_str)
            return 0

    def filter_imdb_fav_dir(self):

        if self.config_enable_favorites == "yes" and self.config_fav_dir:

            # convert comma seperated string into list and remove spaces from comma seperated values using list comprehension
            fav_dir_list = [x.strip().lower() for x in self.config_fav_dir.split(',')]

            # convert lists to lowercase using list comprehension
            imdb_movie_directors = [item.lower() for item in self.imdb_movie_directors]

            # use set.intersection to compare items in lists
            matching_items = set(imdb_movie_directors)
            matching_items = matching_items.intersection(fav_dir_list)

            if set(matching_items):

                self.download_details_dict["filter_imdb_fav_dir_result"] = [1, "Director", "Exception - IMDb Director is in Favorite list"]
                mg_log.info(u"Filter IMDb - Director %s is in Favorite list, proceed" % self.imdb_movie_directors_str)
                return 1

            else:

                mg_log.info(u"Filter IMDb - Director %s is NOT in Favorite list, skip" % self.imdb_movie_directors_str)
                return 0

        else:

            return 0

    def filter_imdb_fav_writer(self):

        if self.config_enable_favorites == "yes" and self.config_fav_writer:

            # convert comma seperated string into list and remove spaces from comma seperated values using list comprehension
            fav_writer_list = [x.strip().lower() for x in self.config_fav_writer.split(',')]

            # convert lists to lowercase using list comprehension
            imdb_movie_writers = [item.lower() for item in self.imdb_movie_writers]

            # use set.intersection to compare items in lists
            matching_items = set(imdb_movie_writers)
            matching_items = matching_items.intersection(fav_writer_list)

            if set(matching_items):

                self.download_details_dict["filter_imdb_fav_writer_result"] = [1, "Writer", "Exception - IMDb Writer is in Favorite list"]
                mg_log.info(u"Filter IMDb - Writer %s is in Favorite list, proceed" % self.imdb_movie_writers_str)
                return 1

            else:

                mg_log.info(u"Filter IMDb - Writer %s is NOT in Favorite list, skip" % self.imdb_movie_writers_str)
                return 0

        else:

            return 0

    def filter_imdb_fav_actor(self):

        if self.config_enable_favorites == "yes" and self.config_fav_actor:

            # convert comma seperated string into list and remove spaces from comma seperated values using list comprehension
            fav_actor_list = [x.strip().lower for x in self.config_fav_actor.split(',')]

            # convert lists to lowercase using list comprehension
            imdb_movie_actors = [item.lower() for item in self.imdb_movie_actors]

            # use set.intersection to compare items in lists
            matching_items = set(imdb_movie_actors)
            matching_items = matching_items.intersection(fav_actor_list)

            if set(matching_items):

                self.download_details_dict["filter_imdb_fav_actor_result"] = [1, "Actor", "Exception - IMDb Actor is in Favorite list"]
                mg_log.info(u"Filter IMDb - Actor %s is in Favorite list, proceed" % self.imdb_movie_actors_str)
                return 1

            else:

                mg_log.info(u"Filter IMDb - Actor %s is NOT in Favorite list, skip" % self.imdb_movie_actors_str)
                return 0

        else:

            return 0

    def filter_imdb_fav_char(self):

        if self.config_enable_favorites == "yes" and self.config_fav_char:

            # convert comma seperated string into list and remove spaces from comma seperated values using list comprehension
            fav_char_list = [x.strip().lower() for x in self.config_fav_char.split(',')]

            # convert lists to lowercase using list comprehension
            imdb_movie_chars = [item.lower() for item in self.imdb_movie_chars]

            # use set.intersection to compare items in lists
            matching_items = set(imdb_movie_chars)
            matching_items = matching_items.intersection(fav_char_list)

            if set(matching_items):

                self.download_details_dict["filter_imdb_fav_char_result"] = [1, "Character", "Exception - IMDb Chracter is in Favorite list"]
                mg_log.info(u"Filter IMDb - Character %s is in Favorite list, proceed" % self.imdb_movie_chars_str)
                return 1

            else:

                mg_log.info(u"Filter IMDb - Character %s is NOT in Favorite list, skip" % self.imdb_movie_chars_str)
                return 0

        else:

            return 0

    def filter_imdb_fav_title(self):

        if self.config_enable_favorites == "yes" and self.config_fav_title:

            # convert comma seperated string into list and remove spaces from comma seperated values using list comprehension
            fav_title_list = [x.strip().lower() for x in self.config_fav_title.split(',')]

            # look for case insensitive string in list using list comprehension
            if self.imdb_movie_title_year.lower() in fav_title_list:

                self.download_details_dict["filter_imdb_fav_title_result"] = [1, "Title", "Exception - IMDb Title is in Favorite list"]
                mg_log.info(u"Filter IMDb - Title %s is in Favorite list, proceed" % self.imdb_movie_title_year)
                return 1

            else:

                mg_log.info(u"Filter IMDb - Title %s is NOT in Favorite list, skip" % self.imdb_movie_title_year)
                return 0

        else:

            return 0

    def filter_imdb_bad_title(self):

        if self.config_enable_favorites == "yes" and self.config_bad_title:

            # convert comma seperated string into list and remove spaces from comma seperated values using list comprehension
            config_bad_title_list = [x.strip().lower() for x in self.config_bad_title.split(',')]

            # look for case insensitive string in list using list comprehension
            if self.imdb_movie_title_year.lower() in config_bad_title_list:

                self.download_details_dict["filter_imdb_bad_title_result"] = [0, "Title", "IMDb - IMDb Title is in Bad list"]
                mg_log.info(u"Filter IMDb - Title %s is in Bad list, skip" % self.imdb_movie_title_year)
                return 0

            else:

                mg_log.info(u"Filter IMDb - Title %s is NOT in Bad list, proceed" % self.imdb_movie_title_year)
                return 1

        else:

            return 1

    def filter_imdb_preferred_genre(self):

        if self.config_enable_preferred == "yes" and self.config_preferred_genre:

            # convert comma seperated string into list and remove spaces from comma seperated values using list comprehension
            preferred_genre_list = [x.strip().lower() for x in self.config_preferred_genre.split(',')]

            # convert lists to lowercase using list comprehension
            imdb_movie_genres = [item.lower() for item in self.imdb_movie_genres]

            # use set.intersection to compare items in lists
            matching_items = set(imdb_movie_genres)
            matching_items = matching_items.intersection(preferred_genre_list)

            if set(matching_items):

                mg_log.info(u"Filter IMDb - Genre %s is in Preferred Genres list, proceed" % self.imdb_movie_genres_str)
                return 1

            else:

                mg_log.info(u"Filter IMDb - Genre %s is NOT in Preferred Genres list, skip" % self.imdb_movie_genres_str)
                return 0

        else:

            return 0

    def filter_imdb_queue_date(self):

        # this is set to queue movies with a maximum defined year (min is GoodDate)
        if self.config_enable_queuing == "yes":

            if self.imdb_movie_year_int <= self.config_queue_date_int:

                mg_log.info(u"Filter IMDb - Queue Date %s is above threshold, proceed" % self.imdb_movie_year_str)
                return 1

            else:

                mg_log.info(u"Filter IMDb - Queue Date %s is below threshold, skip" % self.imdb_movie_year_str)
                return 0

    def filter_imdb_queue_genre(self):

        if self.config_enable_queuing == "yes" and self.config_queue_genre:

            # convert comma seperated string into list and remove spaces from comma seperated values using list comprehension
            queue_genre_list = [x.strip().lower() for x in self.config_queue_genre.split(',')]

            # convert lists to lowercase using list comprehension
            imdb_movie_genres = [item.lower() for item in self.imdb_movie_genres]

            # use set.intersection to compare items in lists
            matching_items = set(imdb_movie_genres)
            matching_items = matching_items.intersection(queue_genre_list)

            if set(matching_items):

                mg_log.info(u"Filter IMDb - Genre %s is in Queue Genres list, proceed" % self.imdb_movie_genres_str)
                return 1

            else:

                mg_log.info(u"Filter IMDb - Genre %s is NOT in Queue Genres list, skip" % self.imdb_movie_genres_str)
                return 0

        else:

            return 0

    # Find IMDb ID
    ###

    def find_imdb_tt(self, site_name, index_post_movie_title_uri, index_post_movie_year):

        # generate url to find imdb tt number using imdb json
        find_imdb_tt_json_url = "http://www.imdb.com/xml/find?json=1&nr=1&tt=on&q={%s %s}" % (index_post_movie_title_uri, index_post_movie_year)
        mg_log.info(u"%s Index - IMDb find tt URL is %s" % (site_name, find_imdb_tt_json_url))

        # download imdb json (using fake agent)
        status_code, content = metadata_download(find_imdb_tt_json_url, user_agent_chrome)

        if status_code != 200:

            mg_log.warning(u"%s Index - Cannot download metadata from IMDb" % site_name)
            return

        try:

            find_imdb_tt_json = json.loads(content)

        except (ValueError, TypeError, KeyError):

            mg_log.warning(u"%s Index - Site feed parse failed for IMDb" % site_name)
            return

        # if resulting imdb json page is blank then return
        if find_imdb_tt_json == {}:

            mg_log.info(u"%s Index - No match for movie title %s on IMDb json" % (site_name, self.index_post_movie_title))
            return

        # find imdb id
        try:

            imdb_tt_number = find_imdb_tt_json["title_approx"][0]["id"]
            mg_log.info(u"%s Index - IMDb tt from IMDb is %s" % (site_name, imdb_tt_number))

        except (IndexError, KeyError):

            mg_log.info(u"%s Index - Cannot find IMDb ID for movie" % site_name)
            return None

        return imdb_tt_number

    def find_imdb_id_tmdb(self, site_name, index_post_movie_title_uri, index_post_movie_year):

        # tmdb api key
        tmdb_api_key = "1d93addd6def495cec493845cd3b2788"

        # generate url to find tmdb id number
        tmdb_find_id_json_url = "https://api.themoviedb.org/3/search/movie?query=%s&year=%s&api_key=%s" % (index_post_movie_title_uri, index_post_movie_year, tmdb_api_key)
        mg_log.info(u"%s Index - TMDb find id URL is %s" % (site_name, tmdb_find_id_json_url))

        # download tmdb json (used for iphone/android)
        status_code, content = metadata_download(tmdb_find_id_json_url, user_agent_iphone)

        if status_code != 200:

            mg_log.warning(u"%s Index - Site feed download failed for TMDb" % site_name)
            return

        try:

            tmdb_find_id_json = json.loads(content)

        except (ValueError, TypeError, KeyError):

            mg_log.warning(u"%s Index - Site feed parse failed for TMDb" % site_name)
            return

        # if resulting tmdb json page is blank then continue
        if tmdb_find_id_json == {}:

            mg_log.info(u"%s Index - No match for movie title %s on TMDb json" % (site_name, self.index_post_movie_title))
            return None

        # find tmdb id
        try:

            tmdb_movie_id = tmdb_find_id_json["id"]
            mg_log.info(u"%s Index - TMDb id is %s" % (site_name, tmdb_movie_id))

        except (IndexError, KeyError):

            try:

                tmdb_movie_id = tmdb_find_id_json["results"][0]["id"]
                mg_log.info(u"%s Index - TMDb id is %s" % (site_name, tmdb_movie_id))

            except (IndexError, KeyError):

                mg_log.info(u"%s Index - Cannot find TMDb ID for movie" % site_name)
                return None

        # generate url to find imdb tt number using tmdb id number from previous search
        tmdb_find_tt_json_url = "https://api.themoviedb.org/3/movie/%s?api_key=%s" % (tmdb_movie_id, tmdb_api_key)
        mg_log.info(u"%s Index - TMDb find tt URL is %s" % (site_name, tmdb_find_tt_json_url))

        # download tmdb json (used for iphone/android)
        status_code, content = metadata_download(tmdb_find_tt_json_url, user_agent_iphone)

        if status_code != 200:

            mg_log.warning(u"%s Index - Site feed download failed for TMDb" % site_name)
            return

        try:

            tmdb_find_tt_json = json.loads(content)

        except (ValueError, TypeError, KeyError):

            mg_log.warning(u"%s Index - Site feed parse failed for TMDb" % site_name)
            return

        # if resulting tmdb json page is blank then continue
        if tmdb_find_tt_json == {}:

            mg_log.info(u"%s Index - No IMDb ID for movie title %s on TMDb json" % (site_name, self.index_post_movie_title))
            return

        # find imdb id
        try:

            imdb_tt_number = tmdb_find_tt_json["imdb_id"]
            mg_log.info(u"%s Index - IMDb ID from TMDb is %s" % (site_name, imdb_tt_number))

        except KeyError:

            mg_log.info(u"%s Index - Cannot find IMDb ID for movie" % site_name)
            return

        return imdb_tt_number

    # IMDb Movie Details
    ###

    def imdb(self):

        # imdb movie title
        imdb_json_title = self.imdb_json_page["data"]["title"]
        imdb_movie_title = decode_html_entities(imdb_json_title)

        # replace illegal characers from imdb title with hyphens
        imdb_movie_title = re.sub(ur"(?i)\\", "-", imdb_movie_title)
        imdb_movie_title = re.sub(ur"(?i)/", "-", imdb_movie_title)
        imdb_movie_title = re.sub(ur"(?i)(?<!\s):(?!\s)", "-", imdb_movie_title)

        # remove illegal characters from imdb title
        imdb_movie_title = re.sub(ur"[:\"*?<>|]+", "", imdb_movie_title)

        # remove string "IMDb - " from title if present
        self.imdb_movie_title = re.sub(ur"(?i)^imdb[\s-]+", "", imdb_movie_title)

        # imdb movie poster url
        try:

            self.imdb_movie_poster = self.imdb_json_page["data"]["image"]["url"]

            # convert url for thumbnail images (214 x 317)
            self.imdb_movie_poster = re.sub(ur"_V1_.jpg", "_V1_SY317_CR12,0,214,317_.jpg", self.imdb_movie_poster)

        except (KeyError, TypeError):

            self.imdb_movie_poster = ""

        # imdb movie release date
        try:

            imdb_movie_year = self.imdb_json_page["data"]["year"]

            # empty year is string ???? thus raise exception and set defaults
            if str(imdb_movie_year) == "????":

                raise KeyError

            self.imdb_movie_year_int = int(imdb_movie_year)
            self.imdb_movie_year_str = str(imdb_movie_year)

        except (KeyError, TypeError):

            self.imdb_movie_year_int = 1900
            self.imdb_movie_year_str = "-"

        # create imdb name used for webui history and queue (no year or custom separators)
        self.imdb_movie_title_strip = self.imdb_movie_title

        # if append year is enabled then append to imdb movie title
        if self.config_enable_append_year == "yes":

            self.imdb_movie_title = u"%s (%s)" % (self.imdb_movie_title, self.imdb_movie_year_str)

        # create imdb name used for favorite title (append year, space for separator)
        self.imdb_movie_title_year = u"%s (%s)" % (self.imdb_movie_title_strip, self.imdb_movie_year_str)

        # if separator is not space, then replace otherwise ignore
        if self.config_movie_title_separator != "<>":

            # use defined movie title separators (can be spaces, dots, hyphens or underscores)
            self.imdb_movie_title = re.sub(ur"\s", self.config_movie_title_separator, self.imdb_movie_title)

        # imdb movie runtime
        try:

            imdb_movie_runtime = self.imdb_json_page["data"]["runtime"]["time"]
            self.imdb_movie_runtime_int = int(imdb_movie_runtime) / 60
            self.imdb_movie_runtime_str = str(self.imdb_movie_runtime_int)

        except (KeyError, TypeError):

            self.imdb_movie_runtime_int = 0
            self.imdb_movie_runtime_str = "-"

        # imdb movie rating
        try:

            imdb_movie_rating = self.imdb_json_page["data"]["rating"]
            self.imdb_movie_rating_dec = decimal.Decimal(str(imdb_movie_rating)).quantize(decimal.Decimal('.1'))
            self.imdb_movie_rating_str = str(imdb_movie_rating)

        except (KeyError, TypeError):

            self.imdb_movie_rating_dec = decimal.Decimal("0.0").quantize(decimal.Decimal('.1'))
            self.imdb_movie_rating_str = "-"

        # imdb movie votes
        try:

            imdb_movie_votes = self.imdb_json_page["data"]["num_votes"]
            self.imdb_movie_votes_int = int(imdb_movie_votes)
            self.imdb_movie_votes_str = str(imdb_movie_votes)

        except (KeyError, TypeError):

            self.imdb_movie_votes_int = 0
            self.imdb_movie_votes_str = "-"

        # imdb movie director
        try:

            self.imdb_movie_directors = []
            imdb_movie_directors = self.imdb_json_page["data"]["directors_summary"]

            for imdb_movie_directors_item in imdb_movie_directors:

                imdb_movie_director = imdb_movie_directors_item["name"]["name"]
                imdb_movie_director = decode_html_entities(imdb_movie_director)
                self.imdb_movie_directors.append(imdb_movie_director)

            self.imdb_movie_directors_str = ", ".join(self.imdb_movie_directors)

        except (KeyError, TypeError):

            self.imdb_movie_directors = []
            self.imdb_movie_directors_str = "-"

        # imdb movie writers
        try:

            self.imdb_movie_writers = []
            imdb_movie_writers = self.imdb_json_page["data"]["writers_summary"]

            for imdb_movie_writers_item in imdb_movie_writers:

                imdb_movie_writer = imdb_movie_writers_item["name"]["name"]
                imdb_movie_writer = decode_html_entities(imdb_movie_writer)
                self.imdb_movie_writers.append(imdb_movie_writer)

            self.imdb_movie_writers_str = ", ".join(self.imdb_movie_writers)

        except (KeyError, TypeError):

            self.imdb_movie_writers = []
            self.imdb_movie_writers_str = "-"

        # imdb movie actor
        try:

            self.imdb_movie_actors = []
            imdb_movie_actors = self.imdb_json_page["data"]["cast_summary"]

            for imdb_movie_actors_item in imdb_movie_actors:

                imdb_movie_actor = imdb_movie_actors_item["name"]["name"]
                imdb_movie_actor = decode_html_entities(imdb_movie_actor)
                self.imdb_movie_actors.append(imdb_movie_actor)

            self.imdb_movie_actors_str = ", ".join(self.imdb_movie_actors)

        except (KeyError, TypeError):

            self.imdb_movie_actors = []
            self.imdb_movie_actors_str = "-"

        # imdb movie characters
        try:

            self.imdb_movie_chars = []
            imdb_movie_chars = self.imdb_json_page["data"]["cast_summary"]

            for imdb_movie_chars_item in imdb_movie_chars:

                imdb_movie_char = imdb_movie_chars_item["char"]
                imdb_movie_char = decode_html_entities(imdb_movie_char)
                self.imdb_movie_chars.append(imdb_movie_char)

            self.imdb_movie_chars_str = ", ".join(self.imdb_movie_chars)

        except (KeyError, TypeError):

            self.imdb_movie_chars = []
            self.imdb_movie_chars_str = "-"

        # imdb movie description
        try:

            self.imdb_movie_description = self.imdb_json_page["data"]["plot"]["outline"]

        except (KeyError, TypeError):

            self.imdb_movie_description = "-"

        # imdb movie genres
        try:

            self.imdb_movie_genres = self.imdb_json_page["data"]["genres"]
            self.imdb_movie_genres_str = ", ".join(self.imdb_movie_genres)

        except (KeyError, TypeError):

            self.imdb_movie_genres = "-"
            self.imdb_movie_genres_str = "-"

        # imdb movie certificate
        try:

            self.imdb_movie_cert = self.imdb_json_page["data"]["certificate"]["certificate"]
            self.cert_system()

        except (KeyError, TypeError):

            self.imdb_movie_cert = "-"

    # certificate system
    ###

    def cert_system(self):

        post_cert_system = config_instance.config_obj["general"]["post_cert_system"]

        if self.imdb_movie_cert == "GP" or self.imdb_movie_cert == "PG" or self.imdb_movie_cert == "IIA" or self.imdb_movie_cert == "K-8":

            self.imdb_movie_cert = "PG"

        elif self.imdb_movie_cert == "U" or self.imdb_movie_cert == "G":

            if post_cert_system == "uk":

                self.imdb_movie_cert = "U"

            if post_cert_system == "us":

                self.imdb_movie_cert = "G"

        elif self.imdb_movie_cert == "PG-12" or self.imdb_movie_cert == "PG12" or self.imdb_movie_cert == "IIB" or self.imdb_movie_cert == "M" or self.imdb_movie_cert == "PG-13" or self.imdb_movie_cert == "K-13":

            if post_cert_system == "uk":

                self.imdb_movie_cert = "12"

            if post_cert_system == "us":

                self.imdb_movie_cert = "PG-13"

        elif self.imdb_movie_cert == "R" or self.imdb_movie_cert == "15" or self.imdb_movie_cert == "L":

            if post_cert_system == "uk":

                self.imdb_movie_cert = "15"

            if post_cert_system == "us":

                self.imdb_movie_cert = "R"

        elif self.imdb_movie_cert == "NC-17" or self.imdb_movie_cert == "M18" or self.imdb_movie_cert == "18":

            if post_cert_system == "uk":

                self.imdb_movie_cert = "18"

            if post_cert_system == "us":

                self.imdb_movie_cert = "NC-17"

        else:

            self.imdb_movie_cert = "-"

        mg_log.info(u"Cert System - IMDb certificate %s" % self.imdb_movie_cert)

    # poster download
    ###

    def poster_download(self):

        if self.imdb_movie_poster:

            # removes non ascii characters from poster image filename
            imdb_movie_title_ascii = self.imdb_movie_title.encode('ascii', 'ignore')

            # if poster image doesnt exist then proceed
            if not uni_to_byte(os.path.exists(os.path.join(history_thumbnails_dir, u"%s.jpg" % imdb_movie_title_ascii))):

                # download poster image from imdb
                status_code, content = metadata_download(self.imdb_movie_poster, user_agent_iphone)

                if status_code == 200:

                    self.poster_image_file = ""

                else:

                    self.poster_image_file = u"default.jpg"
                    mg_log.warning(u"Poster download failed from IMDb")
                    return

                self.poster_image_file = u"%s.jpg" % imdb_movie_title_ascii

                # create path to images directory
                self.poster_image_path = os.path.join(history_thumbnails_dir, self.poster_image_file)

                # save poster image to history folder
                poster_image_write = open(self.poster_image_path, "wb")
                poster_image_write.write(content)
                poster_image_write.close()

            else:

                self.poster_image_file = u"%s.jpg" % imdb_movie_title_ascii

        else:

            self.poster_image_file = u"default.jpg"

    # email notification
    ###

    def email_notify(self):

        if self.config_enable_email_notify == "yes":

            config_email_server = config_instance.config_obj["email_settings"]["email_server"]
            config_email_server_port = int(config_instance.config_obj["email_settings"]["email_server_port"])
            config_email_server_ssl = config_instance.config_obj["email_settings"]["email_server_ssl"]
            config_email_username = config_instance.config_obj["email_settings"]["email_username"]
            config_email_password = config_instance.config_obj["email_settings"]["email_password"]
            config_email_from = config_instance.config_obj["email_settings"]["email_from"]
            config_email_to = config_instance.config_obj["email_settings"]["email_to"]

            config_webconfig_address = config_instance.config_obj["webconfig"]["address"]
            config_webconfig_port = config_instance.config_obj["webconfig"]["port"]
            config_webconfig_enable_ssl = config_instance.config_obj["webconfig"]["enable_ssl"]

            # create message container
            msg = email.mime.multipart.MIMEMultipart('alternative')
            msg['Subject'] = ("MovieGrabber: %s (%s) - %s" % (uni_to_byte(self.imdb_movie_title_strip), self.imdb_movie_year_str, self.download_result_str))
            msg['From'] = config_email_from
            msg['To'] = config_email_to

            # create the body of the message in html
            html = """
            <html>
            <head><META content="text/html; charset=UTF-8" http-equiv=content-type></head>
            <body>
            <body text="Black">

            <p>
            <b>Title:</b> <a href=%s>%s (%s)</a> %s/10 from %s users""" % (uni_to_byte(self.imdb_link), uni_to_byte(self.imdb_movie_title_strip), self.imdb_movie_year_str, self.imdb_movie_rating_str, self.imdb_movie_votes_str) + """
            </p>

            <p>
            <b>Plot:</b> %s""" % (uni_to_byte(self.imdb_movie_description)) + """
            </p>

            <p>
            <b>Actors:</b> %s""" % (uni_to_byte(self.imdb_movie_actors_str)) + """
            </p>

            <p>
            <b>Genres:</b> %s""" % (uni_to_byte(self.imdb_movie_genres_str)) + """
            </p>

            <p>
            <b>Post:</b> <a href=%s>%s</a> (%s)""" % (uni_to_byte(self.index_post_details), uni_to_byte(self.index_post_title), self.download_method) + """
            </p>

            <p>
            <b>Size:</b> %s""" % (uni_to_byte(self.index_post_size_str)) + """
            </p>"""

            # check to make sure movie is queue and not download (queue release not required if set to download)
            if self.download_result_str == "Queued":

                if config_webconfig_enable_ssl == "yes":

                    webconfig_protocol = "https://"

                else:

                    webconfig_protocol = "http://"

                # create url's for local and remote
                queue_release_external = "%s%s:%s/queue/queue_release?queue_release_id=%s" % (webconfig_protocol, self.external_ip_address, config_webconfig_port, str(self.sqlite_id_queued))
                queue_release_internal = "%s%s:%s/queue/queue_release?queue_release_id=%s" % (webconfig_protocol, config_webconfig_address, config_webconfig_port, str(self.sqlite_id_queued))
                html = html + """
                <p>
                <b>Queue Release Links:</b> <a href=%s>Local</a> | <a href=%s>Remote</a>""" % (uni_to_byte(queue_release_internal), uni_to_byte(queue_release_external)) + """
                </p>

                </body>
                </html>"""

            else:

                html += """
                </body>
                </html>"""

            # record the mime types of text/html
            part1 = email.mime.text.MIMEText(html, "html")

            # attach parts into container (last part is preferred)
            msg.attach(part1)

            try:

                mailserver = smtplib.SMTP(config_email_server, config_email_server_port, timeout=30)
                mailserver.ehlo()

                # this attempts to connect to the smtp server
                if config_email_server_ssl == "yes":

                    mailserver.starttls()

                mailserver.login(config_email_username, config_email_password)
                mailserver.sendmail(config_email_from, config_email_to, msg.as_string())
                mailserver.quit()

            except smtplib.SMTPException, e:

                mg_log.warning(u"SMTP error, response - %s" % (str(e)))

            except smtplib.socket.error, e:

                mg_log.warning(u"SMTP socket error, response - %s" % (str(e)))

    # check filter values
    ###

    def filter_check(self):

        # create empty dictionary to store filter result details from filter methods
        self.download_details_dict = {}

        self.filter_index_bad_report_result = self.filter_index_bad_report()
        self.filter_os_watched_result = self.filter_os_watched()
        self.filter_os_queued_result = self.filter_os_queued()
        self.filter_os_movies_downloaded_result = self.filter_os_movies_downloaded()
        self.filter_os_movies_replace_result = self.filter_os_movies_replace()
        self.filter_os_archive_result = self.filter_os_archive()
        self.filter_os_completed_result = self.filter_os_completed()
        self.filter_imdb_bad_title_result = self.filter_imdb_bad_title()
        self.filter_index_special_cut_result = self.filter_index_special_cut()
        self.filter_index_preferred_group_result = self.filter_index_preferred_group()
        self.filter_imdb_good_ratings_result = self.filter_imdb_good_ratings()
        self.filter_imdb_good_votes_result = self.filter_imdb_good_votes()
        self.filter_imdb_good_genre_result = self.filter_imdb_good_genre()
        self.filter_imdb_good_date_result = self.filter_imdb_good_date()
        self.filter_imdb_fav_dir_result = self.filter_imdb_fav_dir()
        self.filter_imdb_fav_writer_result = self.filter_imdb_fav_writer()
        self.filter_imdb_fav_actor_result = self.filter_imdb_fav_actor()
        self.filter_imdb_fav_char_result = self.filter_imdb_fav_char()
        self.filter_imdb_fav_title_result = self.filter_imdb_fav_title()

        if self.filter_os_movies_replace_result == 1:

            self.filter_check_status = 1

        elif self.filter_index_preferred_group_result == 1:

            self.filter_check_status = 1

        elif self.filter_index_special_cut_result == 1:

            self.filter_check_status = 1

        elif (self.filter_os_movies_downloaded_result == 1 and self.filter_os_archive_result == 1 and self.filter_os_watched_result == 1 and self.filter_os_queued_result == 1 and self.filter_os_completed_result == 1 and self.filter_imdb_bad_title_result == 1 and self.filter_index_bad_report_result == 1) and ((self.filter_imdb_good_ratings_result == 1 and self.filter_imdb_good_votes_result == 1 and self.filter_imdb_good_genre_result == 1 and self.filter_imdb_good_date_result == 1) or (self.filter_imdb_fav_dir_result == 1 or self.filter_imdb_fav_writer_result == 1 or self.filter_imdb_fav_actor_result == 1 or self.filter_imdb_fav_char_result == 1 or self.filter_imdb_fav_title_result == 1)):

            self.filter_check_status = 1

        else:

            self.filter_check_status = 0

    # Sqlite
    ##

    def sqlite_insert(self):

        # set last run time/date
        last_run = time.strftime("%d-%m-%Y %H:%M:%S", time.localtime())
        self.last_run = "%s %s" % (str(last_run), time.tzname[1])

        self.last_run_sort = int(time.strftime("%Y%m%d%H%M%S", time.localtime()))

        # insert details into history table (note sqlite requires decimal values as text)
        sqlite_insert = ResultsDBHistory(self.poster_image_file, self.imdb_link, self.imdb_movie_description, self.imdb_movie_directors_str, self.imdb_movie_writers_str, self.imdb_movie_actors_str, self.imdb_movie_chars_str, self.imdb_movie_genres_str, self.imdb_movie_title_strip, self.imdb_movie_year_int, self.imdb_movie_runtime_int, self.imdb_movie_rating_str, self.imdb_movie_votes_int, self.imdb_movie_cert, self.index_post_date, self.index_post_date_sort, self.index_post_size_str, self.index_post_size_sort, self.index_post_nfo, self.index_post_details, self.index_post_title, self.index_post_title_strip, self.index_download_dict, self.download_result_str, self.imdb_movie_title, self.download_method, self.download_details_dict, self.last_run, self.last_run_sort)

        # add the record to the session object
        sql_session.add(sqlite_insert)

        try:

            # commit of record to history table
            sql_session.commit()
            self.post_title_exists = False

            # remove scoped session
            sql_session.remove()

        except exc.IntegrityError:

            # catch error caused my duplicate postname/postnamestrip (unique flag set on column) and rollback transaction
            sql_session.rollback()
            self.post_title_exists = True

            # remove scoped session
            sql_session.remove()

            return

        # get id for current history item (sqlite id passed to automated download function)
        sqlite_postname = sql_session.query(ResultsDBHistory).filter(ResultsDBHistory.postname == self.index_post_title).first()
        self.sqlite_id_history = sqlite_postname.id

        # remove scoped session
        sql_session.remove()

        if self.download_result_str == "Queued":

            # insert details into queued table (note sqlite requires decimal values as text)
            sqlite_insert = ResultsDBQueued(self.poster_image_file, self.imdb_link, self.imdb_movie_description, self.imdb_movie_directors_str, self.imdb_movie_writers_str, self.imdb_movie_actors_str, self.imdb_movie_chars_str, self.imdb_movie_genres_str, self.imdb_movie_title_strip, self.imdb_movie_year_int, self.imdb_movie_runtime_int, self.imdb_movie_rating_str, self.imdb_movie_votes_int, self.imdb_movie_cert, self.index_post_date, self.index_post_date_sort, self.index_post_size_str, self.index_post_size_sort, self.index_post_nfo, self.index_post_details, self.index_post_title, self.index_post_title_strip, self.index_download_dict, self.download_result_str, self.imdb_movie_title, self.download_method, self.download_details_dict, self.last_run, self.last_run_sort)

            # add the record to the session object
            sql_session.add(sqlite_insert)

            try:

                # commit of record to queued table
                sql_session.commit()
                self.post_title_exists = False

                # remove scoped session
                sql_session.remove()

            except exc.IntegrityError:

                # catch error caused my duplicate postname/postnamestrip (unique flag set on column) and rollback transaction
                sql_session.rollback()
                self.post_title_exists = True

                # remove scoped session
                sql_session.remove()

                return

            # get id for current history item (sqlite id passed to automated download function)
            sqlite_postname = sql_session.query(ResultsDBQueued).filter(ResultsDBQueued.postname == self.index_post_title).first()
            self.sqlite_id_queued = sqlite_postname.id

            # remove scoped session
            sql_session.remove()

    # download result
    ###

    def download_result(self):

        if self.filter_check_status == 0:

            self.download_result_str = "Skipped"

        # this will copy nzb/torrent to queued folder if queue enabled and queue date and queue votes are greater than defined values and not queue genres
        elif self.config_enable_queuing == "yes" and self.filter_imdb_queue_date() == 1 or self.filter_imdb_queue_genre() == 1:

            self.download_result_str = "Queued"

        else:

            self.download_result_str = "Downloading"

    # index sites
    ###

    def newznab_index(self):

        site_name = u"Newznab"

        mg_log.info(u"Newznab Index - Newznab search index started")

        # substitute friendly names for real values for categories
        if self.config_cat == u"all formats":

            self.config_cat = u"2010,2020,2030,2040"

        if self.config_cat == u"other":

            self.config_cat = u"2020"

        if self.config_cat == u"foreign":

            self.config_cat = u"2010"

        if self.config_cat == u"divx/xvid":

            self.config_cat = u"2030"

        if self.config_cat == u"hd/x264":

            self.config_cat = u"2040"

        # remove slash at end of hostname if present
        self.config_hostname = re.sub(ur"/+$", "", self.config_hostname)

        # add http:// to hostname if hostname not prefixed with either http or https
        if not re.compile(ur"^http://", re.IGNORECASE).search(self.config_hostname) and not re.compile(ur"^https://", re.IGNORECASE).search(self.config_hostname):

            self.config_hostname = u"http://%s" % self.config_hostname

        # use server side search term for rss feed
        if self.config_search_and != "":

            search_term = self.config_search_and

        else:

            search_term = ""

        if search_term != "":

            # convert comma seperated string into list and remove spaces from comma seperated values using list comprehension
            search_term = [x.strip() for x in search_term.split(',')]

            # convert list back to string
            search_term = ','.join(search_term)

            # replace comma with spaces to seperate search terms
            search_term = re.sub(ur",", " ", search_term)

        if self.config_spotweb_support == "yes":

            # hard set spotweb posts to process to prevent page offset loop
            self.config_posts_to_process_int = 50

        for page_offset in range(0, self.config_posts_to_process_int, 50):

            # use server side for config search AND terms
            if search_term == "":

                # generate url for site with no search criteria (server side)
                site_feed = u"%s:%s%s/api?t=movie&apikey=%s&cat=%s&min=%s&max=%s&o=json&extended=1&offset=%s" % (self.config_hostname, self.config_portnumber, self.config_path, self.config_apikey, self.config_cat, self.config_minsize_int, self.config_maxsize_int, page_offset)

            else:

                # generate url for site with must exist search criteria
                site_feed = u"%s:%s%s/api?t=search&q=%s&apikey=%s&cat=%s&min=%s&max=%s&o=json&extended=1&offset=%s" % (self.config_hostname, self.config_portnumber, self.config_path, search_term, self.config_apikey, self.config_cat, self.config_minsize_int, self.config_maxsize_int, page_offset)

            if self.config_spotweb_support == "yes":

                # generate spotweb api search url xml format
                site_feed = u"%s:%s%s/page=newznabapi?t=movie&apikey=%s&cat=%s&min=%s&max=%s&extended=1" % (self.config_hostname, self.config_portnumber, self.config_path, self.config_apikey, self.config_cat, self.config_minsize_int, self.config_maxsize_int)

            # convert to url for feed - need to "safe" more characters for newznab than torrents
            self.site_feed = urllib.quote(uni_to_byte(site_feed), safe=':/&=?%')
            mg_log.info(u"%s Index - Site feed %s" % (site_name, self.site_feed))

            # generate feed details
            self.feed_details(site_name)

    def bitsnoop_index(self):

        site_name = u"BitSnoop"

        mg_log.info(u"%s Index - Search index started" % site_name)

        # substitute friendly names for real values for categories
        if self.config_cat == u"any":

            self.config_cat = u"video"

        if self.config_cat == u"all movies":

            self.config_cat = u"video-movies"

        # remove slash at end of hostname if present
        self.config_hostname = re.sub(ur"/+$", "", self.config_hostname)

        # add http:// to hostname if hostname not prefixed with either http or https
        if not re.compile(ur"^http://", re.IGNORECASE).search(self.config_hostname) and not re.compile(ur"^https://", re.IGNORECASE).search(self.config_hostname):

            self.config_hostname = u"http://%s" % self.config_hostname

        # use server side search term for rss feed
        if self.config_search_and != "":

            search_term = self.config_search_and

        else:

            search_term = ""

        if search_term != "":

            # convert comma seperated string into list and remove spaces from comma seperated values using list comprehension
            search_term = [x.strip() for x in search_term.split(',')]

            # convert list back to string
            search_term = ','.join(search_term)

            # replace comma with spaces to seperate search terms
            search_term = re.sub(ur",", " ", search_term)

        # generate url for site with must exist search criteria
        site_feed = u"%s:%s/search/%s/%s/c/d/1/?fmt=rss" % (self.config_hostname, self.config_portnumber, self.config_cat, search_term)

        # convert to url for feed
        self.site_feed = urllib.quote(uni_to_byte(site_feed), safe=':/=?')
        mg_log.info(u"%s Index - Site feed %s" % (site_name, self.site_feed))

        # generate feed details
        self.feed_details(site_name)

    def torrentsapi_index(self):

        site_name = u"torrentsapi"

        mg_log.info(u"%s Index - Search index started" % site_name)

        # remove slash at end of hostname if present
        self.config_hostname = re.sub(ur"/+$", "", self.config_hostname)

        # add http:// to hostname if hostname not prefixed with either http or https
        if not re.compile(ur"^http://", re.IGNORECASE).search(self.config_hostname) and not re.compile(ur"^https://", re.IGNORECASE).search(self.config_hostname):

            self.config_hostname = u"http://%s" % self.config_hostname

        # generate url for site
        site_feed = u"%s:%s" % (self.config_hostname, self.config_portnumber)

        # convert to url for feed
        self.site_feed = urllib.quote(uni_to_byte(site_feed), safe=':/=?')
        mg_log.info(u"%s Index - Site feed %s" % (site_name, self.site_feed))

        # generate feed details
        self.feed_details(site_name)

    def kickasstorrents_index(self):

        site_name = u"KickAssTorrents"

        mg_log.info(u"%s Index - Search index started" % site_name)

        # substitute friendly names for real values for categories
        if self.config_cat == u"any":

            self.config_cat = u"any"

        if self.config_cat == u"all movies":

            self.config_cat = u"movies"

        if self.config_cat == u"hd/x264":

            self.config_cat = u"highres-movies"

        # remove slash at end of hostname if present
        self.config_hostname = re.sub(ur"/+$", "", self.config_hostname)

        # add http:// to hostname if hostname not prefixed with either http or https
        if not re.compile(ur"^http://", re.IGNORECASE).search(self.config_hostname) and not re.compile(ur"^https://", re.IGNORECASE).search(self.config_hostname):

            self.config_hostname = u"http://%s" % self.config_hostname

        # use server side search term for rss feed
        if self.config_search_and != "":

            search_term = self.config_search_and

        else:

            search_term = ""

        if search_term != "":

            # convert comma seperated string into list and remove spaces from comma seperated values using list comprehension
            search_term = [x.strip() for x in search_term.split(',')]

            # convert list back to string
            search_term = ','.join(search_term)

            # replace comma with spaces to seperate search terms
            search_term = re.sub(ur",", " ", search_term)

        # generate url for site with must exist search criteria
        site_feed = u"%s:%s/usearch/%scategory:%s language:%s seeds:1/?rss=1" % (self.config_hostname, self.config_portnumber, search_term, self.config_cat, self.config_lang)

        # convert to url for feed
        self.site_feed = urllib.quote(uni_to_byte(site_feed), safe=':/=?')
        mg_log.info(u"%s Index - Site feed %s" % (site_name, self.site_feed))

        # generate feed details
        self.feed_details(site_name)

    def piratebay_index(self):

        site_name = u"PirateBay"

        mg_log.info(u"%s Index - Search index started" % site_name)

        # substitute friendly names for real values for categories
        if self.config_cat == u"any":

            self.config_cat = u"0"

        if self.config_cat == u"all movies":

            self.config_cat = u"201"

        if self.config_cat == u"dvd":

            self.config_cat = u"202"

        if self.config_cat == u"hd/x264":

            self.config_cat = u"207"

        # remove slash at end of hostname if present
        self.config_hostname = re.sub(ur"/+$", "", self.config_hostname)

        # add http:// to hostname if hostname not prefixed with either http or https
        if not re.compile(ur"^http://", re.IGNORECASE).search(self.config_hostname) and not re.compile(ur"^https://", re.IGNORECASE).search(self.config_hostname):

            self.config_hostname = u"http://%s" % self.config_hostname

        # construct site rss feed
        site_feed = u"%s:%s/%s" % (self.config_hostname, self.config_portnumber, self.config_cat)

        # convert to uri for feed
        self.site_feed = urllib.quote(uni_to_byte(site_feed), safe=':/')
        mg_log.info(u"%s Index - Site feed %s" % (site_name, self.site_feed))

        # generate feed details
        self.feed_details(site_name)

    def demonoid_index(self):

        site_name = u"Demonoid"

        mg_log.info(u"%s Index - Search index started" % site_name)

        # substitute friendly names for real values for categories
        if self.config_cat == u"any":

            self.config_cat = u"0"

        if self.config_cat == u"all movies":

            self.config_cat = u"1"

        # remove slash at end of hostname if present
        self.config_hostname = re.sub(ur"/+$", "", self.config_hostname)

        # add http:// to hostname if hostname not prefixed with either http or https
        if not re.compile(ur"^http://", re.IGNORECASE).search(self.config_hostname) and not re.compile(ur"^https://", re.IGNORECASE).search(self.config_hostname):

            self.config_hostname = u"https://%s" % self.config_hostname

        # construct site rss feed, note demonoid does NOT support specification of port number
        site_feed = u"%s/rss/%s.xml" % (self.config_hostname, self.config_cat)

        # convert to uri for feed
        self.site_feed = urllib.quote(uni_to_byte(site_feed), safe=':/')
        mg_log.info(u"%s Index - Site feed %s" % (site_name, self.site_feed))

        # generate feed details
        self.feed_details(site_name)

    def rarbg_index(self):

        site_name = u"RARBG"

        mg_log.info(u"%s Index - Search index started" % site_name)

        # remove slash at end of hostname if present
        self.config_hostname = re.sub(ur"/+$", "", self.config_hostname)

        # add http:// to hostname if hostname not prefixed with either http or https
        if not re.compile(ur"^http://", re.IGNORECASE).search(self.config_hostname) and not re.compile(ur"^https://", re.IGNORECASE).search(self.config_hostname):

            self.config_hostname = u"https://%s" % self.config_hostname

        # construct site rss feed
        site_feed = u"%s:%s/rssdd.php?categories=42;44;45;46;47;48" % (self.config_hostname, self.config_portnumber)

        # convert to uri for feed
        self.site_feed = urllib.quote(uni_to_byte(site_feed), safe=':/;=?')
        mg_log.info(u"%s Index - Site feed %s" % (site_name, self.site_feed))

        # generate feed details
        self.feed_details(site_name)

    def monova_index(self):

        site_name = u"Monova"

        mg_log.info(u"%s Index - Search index started" % site_name)

        # substitute friendly names for real values for categories
        if self.config_cat == u"any":

            self.config_cat = u"video"

        if self.config_cat == u"all movies":

            self.config_cat = u"1"

        # remove slash at end of hostname if present
        self.config_hostname = re.sub(ur"/+$", "", self.config_hostname)

        # add http:// to hostname if hostname not prefixed with either http or https
        if not re.compile(ur"^http://", re.IGNORECASE).search(self.config_hostname) and not re.compile(ur"^https://", re.IGNORECASE).search(self.config_hostname):

            self.config_hostname = u"http://%s" % self.config_hostname

        # generate url for site using category only (category hard set to movies)
        site_feed = u"%s:%s/rss/category/%s" % (self.config_hostname, self.config_portnumber, self.config_cat)

        # convert to url for feed
        self.site_feed = urllib.quote(uni_to_byte(site_feed), safe=':/=?&')
        mg_log.info(u"%s Index - Site feed %s" % (site_name, self.site_feed))

        # generate feed details
        self.feed_details(site_name)

    def torrenthound_index(self):

        site_name = u"TorrentHound"

        mg_log.info(u"%s Index - Search index started" % site_name)

        # remove slash at end of hostname if present
        self.config_hostname = re.sub(ur"/+$", "", self.config_hostname)

        # add http:// to hostname if hostname not prefixed with either http or https
        if not re.compile(ur"^http://", re.IGNORECASE).search(self.config_hostname) and not re.compile(ur"^https://", re.IGNORECASE).search(self.config_hostname):

            self.config_hostname = u"http://%s" % self.config_hostname

        # use server side search term for rss feed
        if self.config_search_and != "":

            search_term = self.config_search_and

        else:

            search_term = ""

        if search_term != "":

            # convert comma seperated string into list and remove spaces from comma seperated values using list comprehension
            search_term = [x.strip() for x in search_term.split(',')]

            # convert list back to string
            search_term = ','.join(search_term)

            # replace comma with spaces to seperate search terms
            search_term = re.sub(ur",", " ", search_term)

            # construct site rss feed
            site_feed = u"%s:%s/rss.php?s=%s" % (self.config_hostname, self.config_portnumber, search_term)

        else:

            mg_log.warning(u"%s Index - Search Index site requires search terms, none given" % site_name)
            return

        # convert to uri for feed
        self.site_feed = urllib.quote(uni_to_byte(site_feed), safe=':/?=')
        mg_log.info(u"%s Index - Site feed %s" % (site_name, self.site_feed))

        # generate feed details
        self.feed_details(site_name)

    def limetorrents_index(self):

        site_name = u"LimeTorrents"

        mg_log.info(u"%s Index - Search index started" % site_name)

        # substitute friendly names for real values for categories
        if self.config_cat == u"all movies":

            self.config_cat = u"16"

        # remove slash at end of hostname if present
        self.config_hostname = re.sub(ur"/+$", "", self.config_hostname)

        # add http:// to hostname if hostname not prefixed with either http or https
        if not re.compile(ur"^http://", re.IGNORECASE).search(self.config_hostname) and not re.compile(ur"^https://", re.IGNORECASE).search(self.config_hostname):

            self.config_hostname = u"http://%s" % self.config_hostname

        # construct site rss feed
        site_feed = u"%s:%s/rss/%s" % (self.config_hostname, self.config_portnumber, self.config_cat)

        # convert to uri for feed
        self.site_feed = urllib.quote(uni_to_byte(site_feed), safe=':/')
        mg_log.info(u"%s Index - Site feed %s" % (site_name, self.site_feed))

        # generate feed details
        self.feed_details(site_name)

    def feed_details(self, site_name):

        status_code, content = metadata_download(self.site_feed, self.user_agent)

        if status_code != 200:

            mg_log.warning(u"%s Index - Site feed download failed" % site_name)
            return

        if site_name == u"Newznab":

            try:

                # parse json formatted feed
                site_feed_parse = json.loads(content)
                site_feed_parse = site_feed_parse["channel"]["item"]

            except (ValueError, TypeError, KeyError):

                mg_log.warning(u"%s Index - Site feed parse failed" % site_name)
                return

        elif site_name == u"torrentsapi":

            try:

                # parse json formatted feed
                site_feed_parse = json.loads(content)
                site_feed_parse = site_feed_parse["MovieList"]

            except (ValueError, TypeError, KeyError):

                mg_log.warning(u"%s Index - Site feed parse failed" % site_name)
                return

        elif site_name == u"Demonoid":

            try:

                # parse xml formatted feed
                site_feed_parse = xmltodict.parse(content)
                site_feed_parse = site_feed_parse["feed"]["entry"]
                print site_feed_parse
            except (xmltodict.expat.ExpatError, KeyError, TypeError):

                mg_log.warning(u"%s Index - Site feed parse failed" % site_name)
                return

        else:

            try:

                # parse xml formatted feed
                site_feed_parse = xmltodict.parse(content)
                site_feed_parse = site_feed_parse["rss"]["channel"]["item"]

            except (xmltodict.expat.ExpatError, KeyError, TypeError):

                mg_log.warning(u"%s Index - Site feed parse failed" % site_name)
                return

        # this breaks down the rss feed page into tag sections
        for node in site_feed_parse:

            if not search_index_poison_queue.empty():

                # get task from queue
                search_index_poison_queue.get()

                # send task done and exit function
                search_index_poison_queue.task_done()

                mg_log.info(u"%s Index - Shutting down search index" % site_name)

                return

            # set post title to none
            post_title = None

            # generate post title
            if site_name == u"Newznab":

                try:

                    post_title = node["title"]

                except (KeyError, TypeError, IndexError, AttributeError):

                    post_title = None

            if site_name == u"BitSnoop":

                try:

                    post_title = node["title"]

                except (KeyError, TypeError, IndexError, AttributeError):

                    post_title = None

            if site_name == u"torrentsapi":

                try:

                    post_title = node["torrent_url"]

                except (KeyError, TypeError, IndexError, AttributeError):

                    post_title = None

            if site_name == u"KickAssTorrents":

                try:

                    post_title = node["torrent:fileName"]

                except (KeyError, TypeError, IndexError, AttributeError):

                    post_title = None

            if site_name == u"PirateBay":

                try:

                    post_title = node["title"]

                except (KeyError, TypeError, IndexError, AttributeError):

                    post_title = None

            if site_name == u"Demonoid":

                try:

                    post_title = node["title"]

                except (KeyError, TypeError, IndexError, AttributeError):

                    post_title = None

            if site_name == u"RARBG":

                try:

                    post_title = node["title"]

                except (KeyError, TypeError, IndexError, AttributeError):

                    post_title = None

            if site_name == u"Monova":

                try:

                    post_title = node["title"]

                except (KeyError, TypeError, IndexError, AttributeError):

                    post_title = None

            if site_name == u"TorrentHound":

                try:

                    post_title = node["title"]

                except (KeyError, TypeError, IndexError, AttributeError):

                    post_title = None

            if site_name == u"LimeTorrents":

                try:

                    post_title = node["title"]

                except (KeyError, TypeError, IndexError, AttributeError):

                    post_title = None

            if post_title is not None:

                # remove square brackets and content from start and end of post title
                post_title = re.sub(ur"^\[[^\]]+\]|\[[^\[]+\]$", "", post_title)

                # remove round brackets and content from start and end of post title
                post_title = re.sub(ur"^\([^\)]+\)|\([^\(]+\)$", "", post_title)

                # remove "<seperator>torrent" from end of post title (kat)
                post_title = re.sub(ur"(?i)[\s\._\-]+torrent$", "", post_title)

                # remove "<seperator>mkv" from end of post title (newznab)
                post_title = re.sub(ur"(?i)[\s\._\-]+mkv$", "", post_title)

                # remove "<seperator>subs" from end of post title (newznab)
                post_title = re.sub(ur"(?i)[\s\._\-]+subs$", "", post_title)

                # remove "<seperator><single digit>" from end of post title (newznab)
                post_title = re.sub(ur"[\s\._\-]+\d$", "", post_title)

                # remove seperator from start and end of post title
                post_title = re.sub(ur"^[\s\._\-]+|[\s\._\-]+$", "", post_title)

                self.index_post_title = post_title
                mg_log.info(u"%s Index - Post title is %s" % (site_name, self.index_post_title))

                # search end of post title stopping at period, underscore, hyphen or space as seperator
                index_post_group_search = re.compile(ur"(?i)[^\.\s_\-]+$").search(post_title)

                if index_post_group_search is not None:

                    self.index_post_group = index_post_group_search.group()
                    mg_log.info(u"%s Index - Post release group %s" % (site_name, self.index_post_group))

                    # if post release group in list then skip
                    if self.filter_index_bad_group() == 0:

                        continue

                else:

                    self.index_post_group = u""
                    mg_log.info(u"%s Index - Post release group not found" % site_name)

            else:

                mg_log.info(u"%s Index - Post title not found" % site_name)
                continue

            # create empty dictionary for download url's
            self.index_download_dict = {}

            # generate download link
            if site_name == u"Newznab":

                try:

                    self.index_download_dict["nzb"] = node["link"]
                    mg_log.info(u"%s Index - Post download link %s" % (site_name, node["link"]))

                except (KeyError, TypeError, IndexError, AttributeError):

                    mg_log.info(u"%s Index - Post download link not found" % site_name)
                    continue

            if site_name == u"BitSnoop":

                try:

                    self.index_download_dict["magnet"] = node["torrent"]["magnetURI"]
                    mg_log.info(u"%s Index - Post download link %s" % (site_name, node["torrent"]["magnetURI"]))

                except (KeyError, TypeError, IndexError, AttributeError):

                    pass

                try:

                    self.index_download_dict["torrent"] = node["enclosure"]["@url"]
                    mg_log.info(u"%s Index - Post download link %s" % (site_name, node["enclosure"]["@url"]))

                except (KeyError, TypeError, IndexError, AttributeError):

                    pass

            if site_name == u"KickAssTorrents":

                try:

                    self.index_download_dict["magnet"] = node["torrent:magnetURI"]
                    mg_log.info(u"%s Index - Post download link %s" % (site_name, node["torrent:magnetURI"]))

                except (KeyError, TypeError, IndexError, AttributeError):

                    pass

                try:

                    self.index_download_dict["torrent"] = node["enclosure"]["@url"]
                    mg_log.info(u"%s Index - Post download link %s" % (site_name, node["enclosure"]["@url"]))

                except (KeyError, TypeError, IndexError, AttributeError):

                    pass

            if site_name == u"PirateBay":

                try:

                    self.index_download_dict["magnet"] = node["link"]
                    mg_log.info(u"%s Index - Post download link %s" % (site_name, node["link"]))

                except (KeyError, TypeError, IndexError, AttributeError):

                    mg_log.info(u"%s Index - Post download link not found" % site_name)
                    continue

            if site_name == u"Demonoid":

                try:

                    index_details_link = node["link"]["@href"]
                    index_download_link = re.sub(ur"details", "download", index_details_link)

                    self.index_download_dict["torrent"] = index_download_link
                    mg_log.info(u"%s Index - Post download link %s" % (site_name, index_download_link))

                except (KeyError, TypeError, IndexError, AttributeError):

                    mg_log.info(u"%s Index - Post download link not found" % site_name)
                    continue

            if site_name == u"RARBG":

                try:

                    self.index_download_dict["torrent"] = node["link"]
                    mg_log.info(u"%s Index - Post download link %s" % (site_name, node["link"]))

                except (KeyError, TypeError, IndexError, AttributeError):

                    mg_log.info(u"%s Index - Post download link not found" % site_name)
                    continue

            if site_name == u"Monova":

                try:

                    download_link = node["enclosure"]["@url"]

                    if "http" not in download_link:

                        # need to prepend http: to url
                        download_link = u"http:%s" % download_link

                    self.index_download_dict["torrent"] = download_link
                    mg_log.info(u"%s Index - Post download link %s" % (site_name, download_link))

                except (KeyError, TypeError, IndexError, AttributeError):

                    mg_log.info(u"%s Index - Post download link not found" % site_name)
                    continue

            if site_name == u"TorrentHound":

                try:

                    hash_value = node["info_hash"]
                    self.index_download_dict["torrent"] = u"%s/torrent/%s" % (self.config_hostname, hash_value)
                    mg_log.info(u"%s Index - Post download link %s" % (site_name, u"%s/torrent/%s" % (self.config_hostname, hash_value)))

                except (KeyError, TypeError, IndexError, AttributeError):

                    mg_log.info(u"%s Index - Post download link not found" % site_name)
                    continue

            if site_name == u"LimeTorrents":

                try:

                    self.index_download_dict["torrent"] = node["enclosure"]["@url"]
                    mg_log.info(u"%s Index - Post download link %s" % (site_name, node["enclosure"]["@url"]))

                except (KeyError, TypeError, IndexError, AttributeError):

                    mg_log.info(u"%s Index - Post download link not found" % site_name)
                    continue

            # remove seperators from post title, used for compare
            self.index_post_title_strip = re.sub(ur"[\+\.\s\-_\(\)\[\],]+", "", self.index_post_title)

            # convert to lowercase
            self.index_post_title_strip = self.index_post_title_strip.lower()

            # check if post title strip is in sqlite db
            sqlite_postnamestrip = sql_session.query(ResultsDBHistory).filter(
                ResultsDBHistory.postnamestrip == self.index_post_title_strip).first()

            # remove scoped session
            sql_session.remove()

            # if current postname not in db then proceed, otherwise do check for dl link and then continue to next item
            if sqlite_postnamestrip is not None:

                mg_log.info(u"%s Index - Post title %s in db history table" % (site_name, self.index_post_title))

                # get dict of postdl links for current postname in db
                sqlite_postnamestrip_postdl = sqlite_postnamestrip.postdl

                # compare dictionaries to see if download link already exists in db
                matching_postdl = set(sqlite_postnamestrip_postdl.items()) & set(self.index_download_dict.items())

                # if no matching items then append
                if len(matching_postdl) == 0:

                    # add download link dict to db postdl dict
                    sqlite_postnamestrip_postdl.update(self.index_download_dict)

                    # commit download url append to history table
                    sql_session.commit()

                    mg_log.info(u"%s Index - Download link(s) appended to db postdl dict" % site_name)

                else:

                    mg_log.info(u"%s Index - Download link(s) already in db postdl dict, skipping" % site_name)

                continue

            else:

                mg_log.info(u"%s Index - Post title %s NOT in db history table, proceed" % (site_name, self.index_post_title))

            # if post title filters are not 0 then continue to next post
            if self.filter_index_search_and() != 1 or self.filter_index_search_or() != 1 or self.filter_index_search_not() != 1:

                mg_log.info(u"%s Index - Post title search criteria failed" % site_name)
                continue

            # set seeds/peers to none
            post_seeders = None
            post_peers = None

            # generate post seeders/peers
            if site_name == u"BitSnoop":

                try:

                    post_seeders = node["numSeeders"]

                except (KeyError, TypeError, IndexError, AttributeError):

                    post_seeders = None

                try:

                    post_peers = node["numLeechers"]

                except (KeyError, TypeError, IndexError, AttributeError):

                    post_peers = None

            if site_name == u"KickAssTorrents":

                try:

                    post_seeders = node["torrent:seeds"]

                except (KeyError, TypeError, IndexError, AttributeError):

                    post_seeders = None

                try:

                    post_peers = node["torrent:peers"]

                except (KeyError, TypeError, IndexError, AttributeError):

                    post_peers = None

            if site_name == u"Monova":

                try:

                    post_seeders = node["torrent:seeds"]

                except (KeyError, TypeError, IndexError, AttributeError):

                    post_seeders = None

                try:

                    post_peers = node["torrent:peers"]

                except (KeyError, TypeError, IndexError, AttributeError):

                    post_peers = None

            if site_name == u"TorrentHound":

                try:

                    post_seeders = node["seeders"]

                except (KeyError, TypeError, IndexError, AttributeError):

                    post_seeders = None

                try:

                    post_peers = node["leechers"]

                except (KeyError, TypeError, IndexError, AttributeError):

                    post_peers = None

            if site_name == u"LimeTorrents":

                try:

                    post_description = node["description"]
                    post_seeders_search = re.compile(ur"(?i)(?<=Seeds:\s)[\d]+").search(post_description)

                    if post_seeders_search is not None:

                        post_seeders = post_seeders_search.group()

                    else:

                        post_seeders = None

                except (KeyError, TypeError, IndexError, AttributeError):

                    post_seeders = None

                try:

                    post_description = node["description"]
                    post_peers_search = re.compile(ur"(?i)(?<=Leechers\s)[\d]+").search(post_description)

                    if post_peers_search is not None:

                        post_peers = post_peers_search.group()

                    else:

                        post_peers = None

                except (KeyError, TypeError, IndexError, AttributeError):

                    post_peers = None

            if post_seeders == u"---":

                post_seeders = u"0"

            if post_peers == u"---":

                post_peers = u"0"

            if post_seeders is not None:

                self.index_min_seeds = post_seeders
                self.index_min_seeds_int = int(post_seeders)
                mg_log.info(u"%s Index - Post seed count %s" % (site_name, self.index_min_seeds))

                # run function to check if seed count above threshold
                if self.filter_index_min_seeds() != 1:

                    continue

            else:

                self.index_min_seeds = u""
                mg_log.info(u"%s Index - Post seed count not found" % site_name)

            if post_peers is not None:

                self.index_min_peers = post_peers
                self.index_min_peers_int = int(post_peers)
                mg_log.info(u"%s Index - Post peer count %s" % (site_name, self.index_min_peers))

                # run function to check if peer count above threshold
                if self.filter_index_min_peers() != 1:

                    continue

            else:

                self.index_min_peers = u""
                mg_log.info(u"%s Index - Post peer count not found" % site_name)

            # set imdb tt number to none
            imdb_tt_number = None

            # generate imdb id from index sites
            if site_name == u"Newznab":

                try:

                    imdb_tt_number = (node_item["@attributes"]["value"] for node_item in node["attr"] if node_item["@attributes"]["name"] == "imdb").next()

                except (KeyError, TypeError, IndexError, AttributeError):

                    imdb_tt_number = None

            if site_name == u"Demonoid":

                try:

                    # get summary content, used for imdb tt
                    imdb_tt_number_search = node["summary"]["#text"]

                    # use regex to construct scale and value
                    imdb_tt_number = re.compile("(?<=http://www.imdb.com/title/tt)[0-9]+", re.IGNORECASE).search(imdb_tt_number_search).group()

                except (KeyError, TypeError, IndexError, AttributeError):

                    imdb_tt_number = None

            if imdb_tt_number is not None:

                # if length is equal to 7 then prefix with tt
                if len(imdb_tt_number) == 7:

                    self.imdb_tt_number = u"tt%s" % imdb_tt_number
                    mg_log.info(u"%s Index - IMDb number from index site is %s" % (site_name, imdb_tt_number))

                # if length is 6 then try prefixing with 0 (some posters dont add the leading zero)
                elif len(imdb_tt_number) == 6:

                    self.imdb_tt_number = u"tt0%s" % imdb_tt_number
                    mg_log.info(u"%s Index - IMDb number from index site is %s" % (site_name, imdb_tt_number))

                # if any other length then mark as none
                else:

                    self.imdb_tt_number = None
                    mg_log.info(u"%s Index - Malformed IMDb number %s from index site" % (site_name, imdb_tt_number))

            # if imdb id not found from index site or post description then use imdb or tmdb
            else:

                mg_log.info(u"%s Index - Cannot find IMDb number from index site, using IMDb/TMDb to generate IMDb number" % site_name)

                # remove everything from movie year in post title to end
                self.index_post_movie_title = re.sub(ur"[\.\-_\s\(]+(20[0-9][0-9]|19[0-9][0-9]).*$", "", self.index_post_title)

                # replace dots, hyphens and underscores with spaces
                self.index_post_movie_title = re.sub(ur"[\.\-_]", " ", self.index_post_movie_title)

                # remove any other additional formatting etc, as we then use this clean title to search on imdb/tmdb for the tt number
                self.index_post_movie_title = re.sub(ur"(multi|directors|\d{3,4}p|bdrip|brrip|dvdrip|webrip|web-dl|xvid).*$", "", self.index_post_movie_title, flags=re.IGNORECASE)

                # remove spaces from end of string
                self.index_post_movie_title = re.sub(ur"\s+$", "", self.index_post_movie_title, flags=re.IGNORECASE)
                mg_log.info(u"%s Index - Clean Post title (used by imdb/tmdb) is %s" % (site_name, self.index_post_movie_title))

                # generate year excluding numbers from start of post title
                index_post_movie_year_search = re.compile(ur"(?<!^)(20[0-9][0-9]|19[0-9][0-9])").search(self.index_post_title)

                if index_post_movie_year_search is not None:

                    self.index_post_movie_year = index_post_movie_year_search.group()
                    mg_log.info(u"%s Index - Post title generated movie year is %s" % (site_name, self.index_post_movie_year))

                else:

                    self.index_post_movie_year = ""
                    mg_log.info(u"%s Index - Cannot generate movie year from post %s" % (site_name, self.index_post_title))

                # convert to uri for html find_id
                self.index_post_movie_title_uri = urllib.quote(uni_to_byte(self.index_post_movie_title))

                # attempt to get imdb id using imdb
                self.imdb_tt_number = self.find_imdb_id_tmdb(site_name, self.index_post_movie_title_uri, self.index_post_movie_year)

                # if no imdb id then fallback to tmdb (slower)
                if self.imdb_tt_number is None:

                    mg_log.info(u"%s Index - Failed to get IMDb number from TMDb for post %s, falling back to TMDb" % (site_name, self.index_post_title))

                    # attempt to get imdb id using tmdb
                    self.imdb_tt_number = self.find_imdb_tt(site_name, self.index_post_movie_title_uri, self.index_post_movie_year)

                    # if no imdb id from imdb or tmdb then skip post
                    if self.imdb_tt_number is None:

                        mg_log.warning(u"%s Index - Failed to get IMDb number for post %s, skipping post" % (site_name, self.index_post_title))
                        continue

            # set post size to none
            post_size = None

            # generate post size
            if site_name == u"Newznab":

                try:

                    post_size = (node_item["@attributes"]["value"] for node_item in node["attr"] if node_item["@attributes"]["name"] == "size").next()

                except (KeyError, TypeError, IndexError, AttributeError):

                    post_size = None

            if site_name == u"BitSnoop":

                try:

                    post_size = node["size"]

                except (KeyError, TypeError, IndexError, AttributeError):

                    post_size = None

            if site_name == u"KickAssTorrents":

                try:

                    post_size = node["torrent:contentLength"]

                except (KeyError, TypeError, IndexError, AttributeError):

                    post_size = None

            if site_name == u"PirateBay":

                try:

                    post_size = node["torrent"]["contentLength"]

                except (KeyError, TypeError, IndexError, AttributeError):

                    post_size = None

            if site_name == u"Demonoid":

                try:

                    # get summary content, used for size of post
                    post_summary = node["summary"]["#text"]

                    # use regex to construct scale and value
                    post_size_desc = re.compile("\d{1,3}\,?\d{0,3}\.\d{1,2}\s(MB|GB)", re.IGNORECASE).search(post_summary).group()
                    post_size_scale = re.compile("[a-zA-Z]+$", re.IGNORECASE).search(post_size_desc).group()
                    post_size_value_str = re.compile("[\d\.,]+", re.IGNORECASE).search(post_size_desc).group()

                    # limit decimal precision to x.xx
                    decimal.getcontext().prec = 3

                    if post_size_scale == "MB":

                        # convert from MB to KB
                        post_size = int(decimal.Decimal(post_size_value_str) * 1000000)

                    elif post_size_scale == "GB":

                        # convert from GB to KB
                        post_size = int(decimal.Decimal(post_size_value_str) * 1000000000)

                    else:

                        post_size = None

                except (KeyError, TypeError, IndexError, AttributeError):

                    post_size = None

            if site_name == u"Monova":

                try:

                    post_size = node["torrent:contentLength"]

                except (KeyError, TypeError, IndexError, AttributeError):

                    post_size = None

            if site_name == u"TorrentHound":

                try:

                    post_size = node["size"]

                except (KeyError, TypeError, IndexError, AttributeError):

                    post_size = None

            if site_name == u"LimeTorrents":

                try:

                    post_size = node["size"]

                except (KeyError, TypeError, IndexError, AttributeError):

                    post_size = None

            if post_size is not None:

                # generate size for history/queue sort order
                self.index_post_size_sort = int(post_size)

                # generate size in MB for GoodSize checks
                self.index_post_size_int = int(post_size) / 1048576
                mg_log.info(u"%s Index - Post size check is %s" % (site_name, self.index_post_size_int))

                # if size is greater than 999 MB then convert to GB format
                if self.index_post_size_int > 999:

                    # limit decimal precision to x.xx
                    decimal.getcontext().prec = 3

                    # generate size in GB
                    index_post_size_gb_int = decimal.Decimal(int(post_size)) / 1073741824

                    # append string GB for History/Queue
                    self.index_post_size_str = u"%s GB" % (str(index_post_size_gb_int))
                    mg_log.info(u"%s Index - Post size is %s" % (site_name, self.index_post_size_str))

                else:

                    # append string mb for History/Queue
                    self.index_post_size_str = u"%s MB" % (str(self.index_post_size_int))
                    mg_log.info(u"%s Index - Post size is %s" % (site_name, self.index_post_size_str))

            else:

                self.index_post_size_str = ""
                self.index_post_size_sort = 0
                self.index_post_size_int = 0
                mg_log.info(u"%s Index - Post size not found" % site_name)

            # if size is below min/max then continue to next post
            if self.filter_index_good_size() != 1:

                mg_log.info(u"%s Index - Post Size is NOT within thresholds" % site_name)
                continue

            # set post data to none
            post_date = None

            # generate post date
            if site_name == u"Newznab":

                try:

                    post_date = node["pubDate"]

                except (KeyError, TypeError, IndexError, AttributeError):

                    post_date = None

            if site_name == u"BitSnoop":

                try:

                    post_date = node["pubDate"]

                except (KeyError, TypeError, IndexError, AttributeError):

                    post_date = None

            if site_name == u"KickAssTorrents":

                try:

                    post_date = node["pubDate"]

                except (KeyError, TypeError, IndexError, AttributeError):

                    post_date = None

            if site_name == u"PirateBay":

                try:

                    post_date = node["pubDate"]

                except (KeyError, TypeError, IndexError, AttributeError):

                    post_date = None

            if site_name == u"Demonoid":

                try:

                    post_date = node["updated"]

                except (KeyError, TypeError, IndexError, AttributeError):

                    post_date = None

            if site_name == u"RARBG":

                try:

                    post_date = node["pubDate"]

                except (KeyError, TypeError, IndexError, AttributeError):

                    post_date = None

            if site_name == u"Monova":

                try:

                    post_date = node["added"]

                except (KeyError, TypeError, IndexError, AttributeError):

                    post_date = None

            if site_name == u"TorrentHound":

                try:

                    post_date = node["pubDate"]

                except (KeyError, TypeError, IndexError, AttributeError):

                    post_date = None

            if site_name == u"LimeTorrents":

                try:

                    post_date = node["pubDate"]

                except (KeyError, TypeError, IndexError, AttributeError):

                    post_date = None

            if post_date is not None:

                post_date = re.sub(ur"\s?\+.*", "", post_date)

                post_date_tuple = parse(post_date)

                # reformat time to correct string format - used by post date
                post_date_reformat = post_date_tuple.strftime("%d-%m-%Y %H:%M:%S")
                self.index_post_date = u"%s UTC" % post_date_reformat
                mg_log.info(u"%s Index - Post date %s" % (site_name, self.index_post_date))

                # reformat time to correct string format - used by sort order
                post_date_reformat = post_date_tuple.strftime("%Y%m%d%H%M%S")
                self.index_post_date_sort = int(post_date_reformat)
                mg_log.info(u"%s Index - Sort date %s" % (site_name, self.index_post_date_sort))

            else:

                self.index_post_date = u"-"
                self.index_post_date_sort = 0
                mg_log.info(u"%s Index - Post date not found" % site_name)

            # set post nfo to none
            post_nfo = None

            # generate post nfo
            if site_name == u"Newznab":

                try:

                    post_nfo = node["comments"]

                except (KeyError, TypeError, IndexError, AttributeError):

                    post_nfo = None

            if post_nfo is not None:

                self.index_post_nfo = post_nfo
                mg_log.info(u"%s Index - Post nfo url %s" % (site_name, self.index_post_nfo))

            else:

                self.index_post_nfo = ""
                mg_log.info(u"%s Index - Post nfo url not found" % site_name)

            # set post details to none
            post_details = None

            # generate post details
            if site_name == u"Newznab":

                try:

                    post_details = node["comments"]

                except (KeyError, TypeError, IndexError, AttributeError):

                    post_details = None

            if site_name == u"BitSnoop":

                try:

                    post_details = node["link"]

                except (KeyError, TypeError, IndexError, AttributeError):

                    post_details = None

            if site_name == u"KickAssTorrents":

                try:

                    post_details = node["link"]

                except (KeyError, TypeError, IndexError, AttributeError):

                    post_details = None

            if site_name == u"PirateBay":

                try:

                    post_details = node["comments"]

                except (KeyError, TypeError, IndexError, AttributeError):

                    post_details = None

            if site_name == u"Demonoid":

                try:

                    post_details = node["link"]["@href"]

                except (KeyError, TypeError, IndexError, AttributeError):

                    post_details = None

            if site_name == u"Monova":

                try:

                    post_details = node["link"]


                    if "https" not in post_details:

                        # need to prepend https: to url
                        post_details = u"https:%s" % post_details

                except (KeyError, TypeError, IndexError, AttributeError):

                    post_details = None

            if site_name == u"TorrentHound":

                try:

                    post_details = node["link"]

                except (KeyError, TypeError, IndexError, AttributeError):

                    post_details = None

            if site_name == u"LimeTorrents":

                try:

                    post_details = node["link"]

                except (KeyError, TypeError, IndexError, AttributeError):

                    post_details = None

            if post_details is not None:

                self.index_post_details = post_details
                mg_log.info(u"%s Index - Post details %s" % (site_name, self.index_post_details))

            else:

                self.index_post_details = ""
                mg_log.info(u"%s Index - Post details not found" % site_name)

            # set post id to none
            post_id = None

            # generate post id
            if site_name == u"Newznab":

                try:

                    post_id = node["guid"]

                except (KeyError, TypeError, IndexError, AttributeError):

                    post_id = None

            if site_name == u"BitSnoop":

                try:

                    post_id = node["torrent"]["infoHash"]

                except (KeyError, TypeError, IndexError, AttributeError):

                    post_id = None

            if site_name == u"Monova":

                try:

                    post_id = node["torrent:infoHash"]

                except (KeyError, TypeError, IndexError, AttributeError):

                    post_id = None

            if site_name == u"KickAssTorrents":

                try:

                    post_id = node["torrent:infoHash"]

                except (KeyError, TypeError, IndexError, AttributeError):

                    post_id = None

            if site_name == u"PirateBay":

                try:

                    post_id = node["torrent"]["infoHash"]

                except (KeyError, TypeError, IndexError, AttributeError):

                    post_id = None

            if site_name == u"TorrentHound":

                try:

                    post_id = node["info_hash"]

                except (KeyError, TypeError, IndexError, AttributeError):

                    post_id = None

            if site_name == u"Demonoid":

                try:

                    post_id = node["id"]

                except (KeyError, TypeError, IndexError, AttributeError):

                    post_id = None

            if post_id is not None:

                self.index_post_id = post_id
                mg_log.info(u"%s Index - Post id %s" % (site_name, self.index_post_id))

            else:

                self.index_post_id = ""
                mg_log.info(u"%s Index - Post id not found" % site_name)

            # call imdb search json
            self.imdb_details_json()

    def imdb_details_json(self):

        # create imdb json feed (used for iphone/android)
        imdb_json = u"http://app.imdb.com/title/maindetails?api=v1&appid=iphone1&locale=en_US&timestamp=1286888328&tconst=%s&sig=app1" % self.imdb_tt_number
        mg_log.info(u"IMDb JSON URL is %s" % imdb_json)

        # generate imdb links for history/queued/email
        self.imdb_link = u"http://www.imdb.com/title/%s" % self.imdb_tt_number
        mg_log.info(u"Post IMDb link %s" % self.imdb_link)

        # download imdb json (used for iphone/android)
        status_code, content = metadata_download(imdb_json, user_agent_iphone)

        if status_code != 200:

            mg_log.warning(u"IMDb - Site feed download failed for IMDb")
            return

        try:

            self.imdb_json_page = json.loads(content)

        except (ValueError, TypeError, KeyError):

            mg_log.warning(u"IMDb - Site feed parse failed for IMDb")
            return

        # run function to create imdb details
        self.imdb()

        # run fuction to check against filter functions
        self.filter_check()

        # run function to download thumbnail poster from imdb
        self.poster_download()

        # run function to create results string
        self.download_result()

        # run function to insert details into history db passing procresult
        self.sqlite_insert()

        # check passes filter checks and also second check for post title already exists in db
        if self.filter_check_status == 1 and self.post_title_exists is False:

            if self.download_result_str == "Downloading":

                # send dictionary of id and table name to download function
                download_details_queue.put(dict(sqlite_table="history", sqlite_id=[self.sqlite_id_history]))

                # run download nzb/torrent thread
                DownloadThread().run()

            if self.config_enable_email_notify == "yes":

                # run function to send email for queued/downloaded
                self.email_notify()

            if self.config_enable_xbmc == "yes":

                # run function to send xbmc gui notification for queued/downloaded
                XBMC().xbmc_gui_notify(self.imdb_movie_title_strip, self.imdb_movie_year_str, self.download_result_str)


# post processing
###


class PostProcessing(object):

    def __init__(self):

        # read config.ini entries
        self.config_post_rename_files = config_instance.config_obj["general"]["post_rename_files"]
        self.config_post_rule = config_instance.config_obj["post_processing"]["post_rule"]
        self.config_post_replace_existing = config_instance.config_obj["general"]["post_replace_existing"]
        self.config_movie_title_separator = config_instance.config_obj["general"]["movie_title_separator"]
        self.config_completed_dir = config_instance.config_obj["folders"]["usenet_completed_dir"]
        self.config_completed_dir = os.path.normpath(self.config_completed_dir)

    def run(self):

        mg_log.info(u"Post processing started")

        # run function to check for post processing enabled, folder is in completed and valid movie file types exist
        self.checks()

        mg_log.info(u"Post processing stopped")

    def checks(self):

        # substitute config parser item for "space"
        if self.config_movie_title_separator == "<>":

            self.config_movie_title_separator = u" "

        if self.config_completed_dir:

            # select download name from history table where download status is downloaded and sort by process date (should help prevent getting wrong postname for duplicate downloads)
            self.sqlite_history_downloaded = sql_session.query(ResultsDBHistory).filter(ResultsDBHistory.dlstatus == "Downloaded").order_by(desc(ResultsDBHistory.procdatesort)).all()

            # remove scoped session
            sql_session.remove()

            # if no movies set to downloaded in db then return
            if self.sqlite_history_downloaded is None:

                return

            else:

                # loop over list of history items and match against existing folders in completed
                for sqlite_history_downloaded_item in self.sqlite_history_downloaded:

                    if not post_processing_poison_queue.empty():

                        # send task done and exit function
                        post_processing_poison_queue.task_done()
                        mg_log.info(u"Shutting down post processing")

                        return

                    # get downloaded imdb name for movies with status of downloaded
                    self.sqlite_history_downloaded_dlname = sqlite_history_downloaded_item.dlname

                    # get post name for movies with status of downloaded
                    self.sqlite_history_downloaded_postname = sqlite_history_downloaded_item.postname

                    # read movie certificate and title
                    self.sqlite_history_downloaded_imdbcert = sqlite_history_downloaded_item.imdbcert.lower()

                    # select imdb genre from history table for current movie download title
                    self.sqlite_history_downloaded_imdbgenre = sqlite_history_downloaded_item.imdbgenre.lower()

                    # use string (from webui dropdown2) to mathematical self.post_processing_operators module
                    self.post_processing_operators = {
                        'equal to': operator.eq,
                        'not equal to': operator.ne,
                        'greater than': operator.gt,
                        'less than': operator.lt}

                    # create variable for completed folder and movie title folder
                    self.os_movie_path_folder = os.path.join(self.config_completed_dir, self.sqlite_history_downloaded_dlname)
                    self.os_movie_path_folder = os.path.normpath(self.os_movie_path_folder)

                    # this is set to check the completed folder for movie downloaded exists
                    if uni_to_byte(os.path.exists(self.os_movie_path_folder)):

                        # run method to move all files to root movie folder
                        mg_log.info(u"Running move to completed movie root folder")
                        self.move()

                        # if rename defined then run method
                        if self.config_post_rename_files != "existing":

                            mg_log.info(u"Running rename movie file to imdb/postname")
                            self.rename()

                        # if rules defined then run method
                        if self.config_post_rule:

                            mg_log.info(u"Running defined user post processing rules")
                            self.rules()

    def move(self):

        # walk completed dir and imdb movie title
        for folder, subs, files in uni_to_byte(os.walk(self.os_movie_path_folder, topdown=False)):

            for os_movie_files_item in files:

                source_path_with_filename = os.path.join(folder, os_movie_files_item)
                dest_path_with_filename = os.path.join(self.os_movie_path_folder, os_movie_files_item)

                if not uni_to_byte(os.path.exists(dest_path_with_filename)):

                    try:

                        # move movie file to movie root folder
                        shutil.move(source_path_with_filename, dest_path_with_filename)
                        mg_log.info(u"Moved file from %s to %s" % (source_path_with_filename, dest_path_with_filename))

                    except IOError:

                        mg_log.warning(u"Cannot move filename to %s" % dest_path_with_filename)

            # remove all sub directories from movie folder (excluding movie folder root)
            if os.path.isdir(folder) and folder != self.os_movie_path_folder:

                shutil.rmtree(folder)

    def rename(self):

        # create list of valid movie file extensions
        valid_extensions_list = [u".mkv", u".avi", u".mp4", u".dvx", u".wmv", u".mov"]

        # walk completed dir and imdb movie title
        for folder, subs, files in uni_to_byte(os.walk(self.os_movie_path_folder, topdown=False)):

            for os_movie_files_item in files:

                # generate filename and file extension
                os_movie_files, os_movie_files_ext = os.path.splitext(os_movie_files_item)

                # check file extension in valid movie file extension list
                if os_movie_files_ext in valid_extensions_list:

                    source_path_with_filename = os.path.join(folder, os_movie_files_item)

                    # if post rename set to imdb then set destination filename to imdb name
                    if self.config_post_rename_files == "imdb":

                        dest_path_with_filename = os.path.join(self.os_movie_path_folder, u"%s%s" % (self.sqlite_history_downloaded_dlname, os_movie_files_ext))

                    # if post rename set to postname then set destination filename to post title
                    elif self.config_post_rename_files == "postname":

                        dest_path_with_filename = os.path.join(self.os_movie_path_folder, u"%s%s" % (self.sqlite_history_downloaded_postname, os_movie_files_ext))

                    if not uni_to_byte(os.path.exists(dest_path_with_filename)):

                        try:

                            os.rename(source_path_with_filename, dest_path_with_filename)
                            mg_log.info(u"Renamed file from %s to %s" % (source_path_with_filename, dest_path_with_filename))

                        except IOError:

                            mg_log.warning(u"Cannot rename filename to %s" % dest_path_with_filename)

    def rules(self):

        # this is set to check the completed folder for movie downloaded exists
        if uni_to_byte(os.path.exists(self.os_movie_path_folder)):

            # convert comma seperated string into list - config parser cannot deal with lists
            config_post_rule_list = self.config_post_rule.split(",")

            for config_post_rule_item in config_post_rule_list:

                # read rule dropdown and textbox values
                self.config_post_rule_dropdown1 = config_instance.config_obj["post_processing"]["%s_dropdown1" % config_post_rule_item]
                self.config_post_rule_dropdown2 = config_instance.config_obj["post_processing"]["%s_dropdown2" % config_post_rule_item]
                self.config_post_rule_dropdown3 = config_instance.config_obj["post_processing"]["%s_dropdown3" % config_post_rule_item]
                self.config_post_rule_textbox1 = config_instance.config_obj["post_processing"]["%s_textbox1" % config_post_rule_item]
                self.config_post_rule_textbox2 = config_instance.config_obj["post_processing"]["%s_textbox2" % config_post_rule_item]

                # walk completed dir and imdb movie title, need to walk again due to move and rename functions previously applied
                for folder, subs, files in uni_to_byte(os.walk(self.os_movie_path_folder, topdown=False)):

                    # create filenames list - folder and subs not required as deleted in move function
                    self.os_movie_filenames_list = files

                    if self.config_post_rule_dropdown1 == "filename":

                        mg_log.info(u"Running filename function")

                        # call filename function - return 1 for matching the operator, 0 for not
                        rules_filename_result = self.rules_filename()

                        if rules_filename_result == 1:

                            # call proceed function
                            self.rules_proceed()

                    elif self.config_post_rule_dropdown1 == "extension":

                        mg_log.info(u"Running extension function")

                        # call extension function - return 1 for matching the operator, 0 for not
                        rules_extension_result = self.rules_extension()

                        if rules_extension_result == 1:

                            # call proceed function
                            self.rules_proceed()

                    elif self.config_post_rule_dropdown1 == "genre":

                        mg_log.info(u"Running genre function")

                        # call genre function - return 1 for matching the operator, 0 for not
                        rules_genre_result = self.rules_genre()

                        if rules_genre_result == 1:

                            # call proceed function
                            self.rules_proceed()

                    elif self.config_post_rule_dropdown1 == "size":

                        mg_log.info(u"Running size function")

                        # call size function - return 1 for matching the operator, 0 for not
                        rules_size_result = self.rules_size()

                        if rules_size_result == 1:

                            # call proceed function
                            self.rules_proceed()

                    elif self.config_post_rule_dropdown1 == "certificate":

                        mg_log.info(u"Running certificate function")

                        # if movie cert "-" then skip otherwise run functions
                        if self.sqlite_history_downloaded_imdbcert != "-":

                            # call certification function
                            rules_certificate_result = self.rules_certificate()

                            if rules_certificate_result == 1:

                                # call proceed function
                                self.rules_proceed()

    def rules_filename(self):

        self.files_to_process = []

        # loop over list of files in folders
        for os_movie_filenames in self.os_movie_filenames_list:

            # create absolute path for files
            os_movie_path_filenames = os.path.join(self.os_movie_path_folder, os_movie_filenames)
            os_movie_path_filenames = os.path.normpath(os_movie_path_filenames)

            # get filename from files located in completed folder, strip extension and convert to lower case
            search = os.path.splitext(os_movie_filenames)[0].lower()

            # replace comma's with regex OR symbol
            self.config_post_rule_textbox1 = re.sub(ur"[,\s?]+", "|", self.config_post_rule_textbox1)

            # perform regex search (partial match)
            if re.compile(self.config_post_rule_textbox1, re.IGNORECASE).search(search):

                if self.config_post_rule_dropdown2 == "equal to":

                    # append matching filenames to list
                    self.files_to_process.append(os_movie_path_filenames)

            else:

                if self.config_post_rule_dropdown2 == "not equal to":

                    # append matching filenames to list
                    self.files_to_process.append(os_movie_path_filenames)

        if self.files_to_process:

            return 1

        else:

            return 0

    def rules_extension(self):

        self.files_to_process = []

        # loop over list of files in folders
        for os_movie_filenames in self.os_movie_filenames_list:

            # create absolute path for files
            os_movie_path_filenames = os.path.join(self.os_movie_path_folder, os_movie_filenames)
            os_movie_path_filenames = os.path.normpath(os_movie_path_filenames)

            # get extension from files located in completed folder, strip filename and convert to lower case
            search = os.path.splitext(os_movie_filenames)[1][1:].lower()

            # replace comma's with regex OR symbol
            self.config_post_rule_textbox1 = re.sub(ur"[,\s?]+", "|", self.config_post_rule_textbox1)

            # perform regex search (partial match)
            if re.compile(self.config_post_rule_textbox1, re.IGNORECASE).search(search):

                if self.config_post_rule_dropdown2 == "equal to":

                    # append matching filenames to list
                    self.files_to_process.append(os_movie_path_filenames)

            else:

                if self.config_post_rule_dropdown2 == "not equal to":

                    # append matching filenames to list
                    self.files_to_process.append(os_movie_path_filenames)

        if self.files_to_process:

            return 1

        else:

            return 0

    def rules_size(self):

        self.files_to_process = []

        # loop over list of files in folders
        for os_movie_filenames in self.os_movie_filenames_list:

            # create absolute path for files
            os_movie_path_filenames = os.path.join(self.os_movie_path_folder, os_movie_filenames)
            os_movie_path_filenames = os.path.normpath(os_movie_path_filenames)

            mg_log.info(u"Checking file size for file %s" % os_movie_path_filenames)

            # get size of file in movie folder located in completed folder, convert to integer
            os_movie_path_filenames_size = os.path.getsize(os_movie_path_filenames)
            path_filenames_int = int(os_movie_path_filenames_size)

            # convert from bytes to megabytes
            path_filenames_int /= 1000000

            mg_log.info(u"File size is %s MB" % (str(path_filenames_int)))

            # convert to integer
            config_post_rule_textbox1_int = int(self.config_post_rule_textbox1)

            # use mathematical operator in dropdown2 to evaluate textbox1 against file size in movie folder
            if self.post_processing_operators[self.config_post_rule_dropdown2](path_filenames_int, config_post_rule_textbox1_int):

                # append matching filenames to list
                self.files_to_process.append(os_movie_path_filenames)

        if self.files_to_process:

            return 1

        else:

            return 0

    def rules_genre(self):

        # replace comma's with regex OR symbol
        self.config_post_rule_textbox1 = re.sub(ur"[,\s?]+", "|", self.config_post_rule_textbox1)

        # perform regex search (partial match)
        if re.compile(self.config_post_rule_textbox1, re.IGNORECASE).search(self.sqlite_history_downloaded_imdbgenre):

            if self.config_post_rule_dropdown2 == "equal to":

                self.files_to_process = self.os_movie_filenames_list
                return 1

            else:

                return 0

        else:

            if self.config_post_rule_dropdown2 == "not equal to":

                self.files_to_process = self.os_movie_filenames_list
                return 1

            else:

                return 0

    def rules_certificate(self):

        def cert_int(cert_string):

            cert_string_int = None

            if cert_string == "u" or cert_string == "g":

                cert_string_int = 1

            if cert_string == "pg":

                cert_string_int = 2

            if cert_string == "12" or cert_string == "pg-13":

                cert_string_int = 3

            if cert_string == "15" or cert_string == "r":

                cert_string_int = 4

            if cert_string == "18" or cert_string == "nc-17":

                cert_string_int = 5

            return cert_string_int

        # run function to convert movie cert and rule textbox1 to integer value
        config_post_rule_textbox1_int = cert_int(self.config_post_rule_textbox1)
        movie_download_cert_int = cert_int(self.sqlite_history_downloaded_imdbcert)

        # use mathematical operator in dropdown2 to evaluate rule textbox1 against certificate for movie
        if self.post_processing_operators[self.config_post_rule_dropdown2](movie_download_cert_int, config_post_rule_textbox1_int):

            self.files_to_process = self.os_movie_filenames_list
            return 1

        else:

            return 0

    def rules_move(self):

        # create variable for self.config_post_rule_textbox2 and movie title folder
        destination_move_path = os.path.join(self.config_post_rule_textbox2, self.sqlite_history_downloaded_dlname)
        destination_move_path = os.path.normpath(destination_move_path)

        mg_log.info(u"Source folder is %s" % self.os_movie_path_folder)
        mg_log.info(u"Destination folder is %s" % destination_move_path)

        if self.config_post_replace_existing == "no" and uni_to_byte(os.path.exists(destination_move_path)):

            mg_log.info(u"Cannot move folder %s as destination already exist at %s" % (self.os_movie_path_folder, destination_move_path))

        else:

            try:

                if uni_to_byte(os.path.exists(destination_move_path)):

                    # delete existing destination movie folder
                    shutil.rmtree(destination_move_path)
                    mg_log.info(u"Deleted existing destination folder %s" % destination_move_path)

            except IOError:

                mg_log.warning(u"Cannot delete existing destination folder %s" % destination_move_path)
                return

            try:

                # move movie folder to desination
                shutil.copytree(self.os_movie_path_folder, destination_move_path)
                mg_log.info(u"Copied folder from %s to %s" % (self.os_movie_path_folder, destination_move_path))

            except IOError:

                mg_log.warning(u"Cannot copy folder to %s" % destination_move_path)
                return

            # delete source movie folder
            shutil.rmtree(self.os_movie_path_folder)
            mg_log.info(u"Deleted source folder %s" % self.os_movie_path_folder)

            # run xbmc functions
            self.xbmc_proceed()

    def rules_delete(self):

        # loop over list of files in folders
        for os_movie_filenames in self.files_to_process:

            # create absolute path for files
            os_movie_path_filenames = os.path.join(self.os_movie_path_folder, os_movie_filenames)
            os_movie_path_filenames = os.path.normpath(os_movie_path_filenames)

            # delete files from movie folder
            if uni_to_byte(os.path.exists(os_movie_path_filenames)):

                try:

                    os.remove(os_movie_path_filenames)
                    mg_log.info(u"File deleted %s" % os_movie_path_filenames)

                except IOError:

                    mg_log.warning(u"Cannot delete file %s" % os_movie_path_filenames)
                    continue

    def rules_proceed(self):

        if self.config_post_rule_dropdown3 == "delete":

            mg_log.info(u"Running delete function")

            # call delete function
            self.rules_delete()

        if self.config_post_rule_dropdown3 == "move":

            mg_log.info(u"Running move function")

            # call move function
            self.rules_move()

    def xbmc_proceed(self):

        # read xbmc settings
        xbmc_library_update = config_instance.config_obj["xbmc"]["xbmc_library_update"]

        if xbmc_library_update == "yes":

            mg_log.info(u"Running xbmc library update function")

            # call xbmc library update function
            XBMC().xbmc_library_update()


# webui
###

def header():

    # header information
    template.templates_dir = templates_dir
    template.local_version = config_instance.config_obj["general"]["local_version"]
    template.title = "MovieGrabber %s - %s" % (template.local_version, template.section_name)
    template.strapline = "The only truly automated movie downloader"
    template.skin_color_file = "%s.css" % template.skin_color


def footer():

    # footer information
    template.last_run = config_instance.config_obj["general"]["last_run"]
    template.forum_link = "http://forums.sabnzbd.org/viewtopic.php?f=6&amp;t=8569"


# webgui subfolders
###


class ConfigIMDB(object):

    # read config imdb page
    @cherrypy.expose
    def index(self):

        global template

        template = Template(file=os.path.join(templates_dir, "config_imdb.tmpl"))
        template.section_name = "Config IMDb"

        # read values from config.ini
        template.skin_color = config_instance.config_obj["general"]["skin_color"]
        template.good_rating = float(config_instance.config_obj["imdb"]["good_rating"])
        template.good_date = int(config_instance.config_obj["imdb"]["good_date"])
        template.good_votes = config_instance.config_obj["imdb"]["good_votes"]
        template.good_genre = config_instance.config_obj["imdb"]["good_genre"]
        template.preferred_rating = float(config_instance.config_obj["imdb"]["preferred_rating"])
        template.preferred_genre = config_instance.config_obj["imdb"]["preferred_genre"]
        template.queue_date = int(config_instance.config_obj["imdb"]["queue_date"])
        template.queue_genre = config_instance.config_obj["imdb"]["queue_genre"]
        template.bad_title = config_instance.config_obj["imdb"]["bad_title"]
        template.fav_dir = config_instance.config_obj["imdb"]["fav_dir"]
        template.fav_writer = config_instance.config_obj["imdb"]["fav_writer"]
        template.fav_actor = config_instance.config_obj["imdb"]["fav_actor"]
        template.fav_char = config_instance.config_obj["imdb"]["fav_char"]
        template.fav_title = config_instance.config_obj["imdb"]["fav_title"]
        template.genre_list_all = ["action", "adventure", "animation", "biography", "comedy", "crime", "documentary", "drama", "family", "fantasy", "film-Noir", "game-show", "history", "horror", "music", "musical", "mystery", "news", "reality-tv", "romance", "sci-fi", "short", "sport", "talk-show", "thriller", "war", "western"]

        # convert comma seperated string into list - config parser cannot deal with lists
        template.good_genre_list_selected = template.good_genre.split(",")

        # convert comma seperated string into list - config parser cannot deal with lists
        template.preferred_genre_list_selected = template.preferred_genre.split(",")

        # convert comma seperated string into list - config parser cannot deal with lists
        template.queue_genre_list_selected = template.queue_genre.split(",")

        header()

        footer()

        return str(template)

    # save config imdb form
    @cherrypy.expose
    def save_config_imdb(self, **kwargs):

        # write values to config.ini
        config_instance.config_obj["imdb"]["good_date"] = kwargs["good_date2"]
        config_instance.config_obj["imdb"]["good_rating"] = kwargs["good_rating2"]
        config_instance.config_obj["imdb"]["good_date"] = kwargs["good_date2"]
        config_instance.config_obj["imdb"]["queue_date"] = kwargs["queue_date2"]
        config_instance.config_obj["imdb"]["bad_title"] = del_inv_chars(kwargs["bad_title2"])
        config_instance.config_obj["imdb"]["fav_dir"] = del_inv_chars(kwargs["fav_dir2"])
        config_instance.config_obj["imdb"]["fav_writer"] = del_inv_chars(kwargs["fav_writer2"])
        config_instance.config_obj["imdb"]["fav_actor"] = del_inv_chars(kwargs["fav_actor2"])
        config_instance.config_obj["imdb"]["fav_char"] = del_inv_chars(kwargs["fav_char2"])
        config_instance.config_obj["imdb"]["fav_title"] = del_inv_chars(kwargs["fav_title2"])

        if kwargs["good_votes2"]:

            # check value is an integer, if not do not save
            try:
                int(kwargs["good_votes2"])
                config_instance.config_obj["imdb"]["good_votes"] = kwargs["good_votes2"]

            except ValueError:

                pass

        genre_list_all = ["action", "adventure", "animation", "biography", "comedy", "crime", "documentary", "drama", "family", "fantasy", "film-Noir", "game-show", "history", "horror", "music", "musical", "mystery", "news", "reality-tv", "romance", "sci-fi", "short", "sport", "talk-show", "thriller", "war", "western"]

        # limit decimal precision to x.x
        decimal.getcontext().prec = 2

        # convert float to string for use with decimal.Decimal
        good_ratings2 = str(kwargs["good_rating2"])
        preferred_rating2 = str(kwargs["preferred_rating2"])

        # if good rating is not 0.0 then change preferred rating to be less than good rating if its set incorrectly
        if decimal.Decimal(good_ratings2) != decimal.Decimal(str(0.0)) and decimal.Decimal(preferred_rating2) >= decimal.Decimal(good_ratings2):

            preferred_rating2 = decimal.Decimal(good_ratings2) - decimal.Decimal(str(0.1))
            config_instance.config_obj["imdb"]["preferred_rating"] = str(preferred_rating2)

        else:

            config_instance.config_obj["imdb"]["preferred_rating"] = str(preferred_rating2)

        # create empty list to store preferred genres
        preferred_genre_list = []

        # loop over all genres and write genres that match to list
        for genre_item in genre_list_all:

            # if good genre or preferred genre not in select list then write blank for preferred genre
            if "good_genre_item_selected2" not in kwargs or "preferred_genre_item_selected2" not in kwargs:

                config_instance.config_obj["imdb"]["preferred_genre"] = ""

            # if genre in good genre select list and genre in preferred genre select list then write genre to preferred genre list
            elif genre_item in kwargs["good_genre_item_selected2"] and genre_item in kwargs["preferred_genre_item_selected2"]:

                preferred_genre_list.append(genre_item)

        # convert list into comma seperated string - config parser cannot deal with lists
        config_instance.config_obj["imdb"]["preferred_genre"] = ",".join(preferred_genre_list)

        # create empty list to store good genres
        good_genre_list = []

        # loop over all genres and write genres that match to list
        for genre_item in genre_list_all:

            # if good genre not in select list then write blank for good genre
            if "good_genre_item_selected2" not in kwargs:

                config_instance.config_obj["imdb"]["good_genre"] = ""

            # if genre in good genre select list then write genre to good genre list
            elif genre_item in kwargs["good_genre_item_selected2"]:

                good_genre_list.append(genre_item)

        # convert list into comma seperated string - config parser cannot deal with lists
        config_instance.config_obj["imdb"]["good_genre"] = ",".join(good_genre_list)

        # create empty list to store queue genres
        queue_genre_list = []

        # loop over all genres and write genres that match to list
        for genre_item in genre_list_all:

            # if good genre or queue genre not in select list then write blank for queue genre
            if "good_genre_item_selected2" not in kwargs or "queue_genre_item_selected2" not in kwargs:

                config_instance.config_obj["imdb"]["queue_genre"] = ""

            # if genre in good genre select list and genre in queue genre select list then write genre to queue genre list
            elif genre_item in kwargs["good_genre_item_selected2"] and genre_item in kwargs["queue_genre_item_selected2"]:

                queue_genre_list.append(genre_item)

        # convert list into comma seperated string - config parser cannot deal with lists
        config_instance.config_obj["imdb"]["queue_genre"] = ",".join(queue_genre_list)

        config_instance.config_obj.write()

        raise cherrypy.HTTPRedirect(".")


class ConfigGeneral(object):

    # read config general page
    @cherrypy.expose
    def index(self):

        global template

        template = Template(file=os.path.join(templates_dir, "config_general.tmpl"))
        template.section_name = "Config General"

        # read values from config.ini
        template.skin_theme = config_instance.config_obj["general"]["skin_theme"]
        template.skin_theme_list = ["classic", "slick"]
        template.skin_color = config_instance.config_obj["general"]["skin_color"]

        if template.skin_theme == "classic":

            template.skin_color_list = ["darkblue", "black", "classic", "green", "lightblue", "red", "white-black"]

        else:

            template.skin_color_list = ["default"]

        template.queue_max_items_shown = config_instance.config_obj["general"]["queue_max_items_shown"]
        template.history_max_items_shown = config_instance.config_obj["general"]["history_max_items_shown"]
        template.max_items_shown_list = ["10", "20", "50", "100", "all"]
        template.launch_browser = config_instance.config_obj["general"]["launch_browser"]
        template.address = config_instance.config_obj["webconfig"]["address"]
        template.port = config_instance.config_obj["webconfig"]["port"]
        template.username = config_instance.config_obj["webconfig"]["username"]
        template.password = config_instance.config_obj["webconfig"]["password"]
        template.enable_ssl = config_instance.config_obj["webconfig"]["enable_ssl"]
        template.log_level = config_instance.config_obj["general"]["log_level"]
        template.log_level_list = ["INFO", "WARNING", "exception"]
        template.check_version = config_instance.config_obj["general"]["check_version"]
        template.check_version_list = ["off", "daily", "weekly"]
        template.movie_title_separator = config_instance.config_obj["general"]["movie_title_separator"]
        template.index_preferred_group = config_instance.config_obj["general"]["index_preferred_group"]
        template.index_special_cut = config_instance.config_obj["general"]["index_special_cut"]
        template.index_bad_group = config_instance.config_obj["general"]["index_bad_group"]
        template.index_bad_report = config_instance.config_obj["general"]["index_bad_report"]
        template.index_posts_to_process = config_instance.config_obj["general"]["index_posts_to_process"]
        template.min_seeds = config_instance.config_obj["general"]["min_seeds"]
        template.min_peers = config_instance.config_obj["general"]["min_peers"]

        # substitute real values for friendly names
        if template.movie_title_separator == "<>":

            template.movie_title_separator = "spaces"

        if template.movie_title_separator == "-":

            template.movie_title_separator = "hyphens"

        if template.movie_title_separator == ".":

            template.movie_title_separator = "dots"

        if template.movie_title_separator == "_":

            template.movie_title_separator = "underscores"

        template.movie_title_separator_list = ["spaces", "hyphens", "dots", "underscores"]

        header()

        footer()

        return str(template)

    # save config general form
    @cherrypy.expose
    def save_config_general(self, **kwargs):

        # write values to config.ini
        config_instance.config_obj["general"]["skin_color"] = kwargs["skin_color2"]
        config_instance.config_obj["general"]["launch_browser"] = kwargs["launch_browser2"]
        config_instance.config_obj["general"]["queue_max_items_shown"] = kwargs["queue_max_items_shown2"]
        config_instance.config_obj["general"]["history_max_items_shown"] = kwargs["history_max_items_shown2"]
        config_instance.config_obj["webconfig"]["username"] = kwargs["username2"]
        config_instance.config_obj["webconfig"]["password"] = kwargs["password2"]
        config_instance.config_obj["webconfig"]["enable_ssl"] = kwargs["enable_ssl2"]
        config_instance.config_obj["general"]["index_preferred_group"] = kwargs["index_preferred_group2"]
        config_instance.config_obj["general"]["index_special_cut"] = kwargs["index_special_cut2"]
        config_instance.config_obj["general"]["index_bad_group"] = kwargs["index_bad_group2"]
        config_instance.config_obj["general"]["index_bad_report"] = kwargs["index_bad_report2"]
        config_instance.config_obj["general"]["log_level"] = kwargs["log_level2"]
        config_instance.config_obj["general"]["check_version"] = kwargs["check_version2"]

        # contruct logger instance and new logging level
        logging_level = getattr(logging, kwargs["log_level2"])

        # change logging level for moviegrabber logger instance
        mg_log.setLevel(logging_level)

        # change logging level for sqlite logger instance
        sql_log.setLevel(logging_level)

        # substitute friendly names for real values for movie separators
        if kwargs["movie_title_separator2"] == "spaces":

            config_instance.config_obj["general"]["movie_title_separator"] = "<>"

        if kwargs["movie_title_separator2"] == "hyphens":

            config_instance.config_obj["general"]["movie_title_separator"] = "-"

        if kwargs["movie_title_separator2"] == "dots":

            config_instance.config_obj["general"]["movie_title_separator"] = "."

        if kwargs["movie_title_separator2"] == "underscores":

            config_instance.config_obj["general"]["movie_title_separator"] = "_"

        if kwargs["address2"]:

            config_instance.config_obj["webconfig"]["address"] = kwargs["address2"]

        if kwargs["port2"]:

            config_instance.config_obj["webconfig"]["port"] = kwargs["port2"]

        if kwargs["index_posts_to_process2"]:

            # check value is an integer, if not do not save
            try:
                int(kwargs["index_posts_to_process2"])
                config_instance.config_obj["general"]["index_posts_to_process"] = kwargs["index_posts_to_process2"]

            except ValueError:

                pass

        else:

            config_instance.config_obj["general"]["index_posts_to_process"] = "50"

        if kwargs["min_seeds2"]:

            # check value is an integer, if not do not save
            try:
                int(kwargs["min_seeds2"])
                config_instance.config_obj["general"]["min_seeds"] = kwargs["min_seeds2"]

            except ValueError:

                pass

        else:

            config_instance.config_obj["general"]["min_seeds"] = "0"

        if kwargs["min_peers2"]:

            # check value is an integer, if not do not save
            try:
                int(kwargs["min_peers2"])
                config_instance.config_obj["general"]["min_peers"] = kwargs["min_peers2"]

            except ValueError:

                pass

        else:

            config_instance.config_obj["general"]["min_peers"] = "0"

        config_instance.config_obj.write()

        raise cherrypy.HTTPRedirect(".")

    # save config general skin theme form
    @cherrypy.expose
    def save_config_general_skin_theme(self, **kwargs):

        config_instance.config_obj["general"]["skin_theme"] = kwargs["skin_theme2"]

        # write settings to config.ini
        config_instance.config_obj.write()

        raise cherrypy.HTTPRedirect(".")


class ConfigSwitches(object):

    # read config switches page
    @cherrypy.expose
    def index(self):

        global template

        template = Template(file=os.path.join(templates_dir, "config_switches.tmpl"))
        template.section_name = "Config Switches"

        # read values from config.ini
        template.skin_color = config_instance.config_obj["general"]["skin_color"]
        template.movies_downloaded_dir = config_instance.config_obj["folders"]["movies_downloaded_dir"]
        template.movies_replace_dir = config_instance.config_obj["folders"]["movies_replace_dir"]
        template.email_server = config_instance.config_obj["email_settings"]["email_server"]
        template.email_server_port = config_instance.config_obj["email_settings"]["email_server_port"]
        template.email_from = config_instance.config_obj["email_settings"]["email_from"]
        template.email_to = config_instance.config_obj["email_settings"]["email_to"]
        template.xbmc_host = config_instance.config_obj["xbmc"]["xbmc_host"]
        template.xbmc_port = config_instance.config_obj["xbmc"]["xbmc_port"]
        template.xbmc_username = config_instance.config_obj["xbmc"]["xbmc_username"]
        template.xbmc_password = config_instance.config_obj["xbmc"]["xbmc_password"]
        template.enable_downloaded = config_instance.config_obj["switches"]["enable_downloaded"]
        template.enable_replace = config_instance.config_obj["switches"]["enable_replace"]
        template.enable_favorites = config_instance.config_obj["switches"]["enable_favorites"]
        template.enable_preferred = config_instance.config_obj["switches"]["enable_preferred"]
        template.enable_queuing = config_instance.config_obj["switches"]["enable_queuing"]
        template.enable_email_notify = config_instance.config_obj["switches"]["enable_email_notify"]
        template.enable_append_year = config_instance.config_obj["switches"]["enable_append_year"]
        template.enable_posters = config_instance.config_obj["switches"]["enable_posters"]
        template.enable_group_filter = config_instance.config_obj["switches"]["enable_group_filter"]
        template.enable_post_processing = config_instance.config_obj["switches"]["enable_post_processing"]

        header()

        footer()

        return str(template)

    # save config switches form
    @cherrypy.expose
    def save_config_switches(self, **kwargs):

        # write values to config.ini
        config_instance.config_obj["switches"]["enable_downloaded"] = kwargs["enable_downloaded2"]
        config_instance.config_obj["switches"]["enable_replace"] = kwargs["enable_replace2"]
        config_instance.config_obj["switches"]["enable_favorites"] = kwargs["enable_favorites2"]
        config_instance.config_obj["switches"]["enable_preferred"] = kwargs["enable_preferred2"]
        config_instance.config_obj["switches"]["enable_queuing"] = kwargs["enable_queuing2"]
        config_instance.config_obj["switches"]["enable_email_notify"] = kwargs["enable_email_notify2"]
        config_instance.config_obj["switches"]["enable_append_year"] = kwargs["enable_append_year2"]
        config_instance.config_obj["switches"]["enable_posters"] = kwargs["enable_posters2"]
        config_instance.config_obj["switches"]["enable_group_filter"] = kwargs["enable_group_filter2"]
        config_instance.config_obj["switches"]["enable_post_processing"] = kwargs["enable_post_processing2"]

        config_instance.config_obj.write()

        raise cherrypy.HTTPRedirect(".")


class ConfigPost(object):

    # read config post processing page
    @cherrypy.expose
    def index(self):

        global template

        template = Template(file=os.path.join(templates_dir, "config_post.tmpl"))
        template.section_name = "Config Post Processing"

        # create variable for templates to read config entries
        template.config_obj = config_instance.config_obj

        template.post_rename_files = config_instance.config_obj["general"]["post_rename_files"]
        template.post_replace_existing = config_instance.config_obj["general"]["post_replace_existing"]
        template.post_cert_system = config_instance.config_obj["general"]["post_cert_system"]
        template.xbmc_library_update = config_instance.config_obj["xbmc"]["xbmc_library_update"]
        template.skin_color = config_instance.config_obj["general"]["skin_color"]

        template.post_rename_files_list = ["existing", "imdb", "postname"]
        template.dropdown1_list = ["select", "filename", "extension", "size", "genre", "certificate"]
        template.dropdown2_list = ["select", "equal to", "not equal to", "greater than", "less than"]
        template.dropdown3_list = ["select", "move", "delete"]

        config_post_rule = config_instance.config_obj["post_processing"]["post_rule"]

        if config_post_rule:

            # convert comma seperated string into list - config parser cannot deal with lists
            template.post_config_rule_list = [x.strip() for x in config_post_rule.split(",")]

        else:

            template.post_config_rule_list = []

        header()

        footer()

        return str(template)

    # save config switches form
    @cherrypy.expose
    def save_config_post(self, **kwargs):

        # write values to config.ini
        config_instance.config_obj["general"]["post_cert_system"] = kwargs["post_cert_system2"]
        config_instance.config_obj["general"]["post_rename_files"] = kwargs["post_rename_files2"]
        config_instance.config_obj["general"]["post_replace_existing"] = kwargs["post_replace_existing2"]
        config_instance.config_obj["xbmc"]["xbmc_library_update"] = kwargs["xbmc_library_update2"]
        config_instance.config_obj.write()

        raise cherrypy.HTTPRedirect(".")

    # add config post processing rules form
    @cherrypy.expose
    def add_config_rule(self):

        # get existing rule list
        config_post_rule = config_instance.config_obj["post_processing"]["post_rule"]

        # set initial rule increment value
        post_rule = 1

        # construct rule name from new value and increment
        add_post_rule = "rule_%s" % (str(post_rule))

        if config_post_rule:

            # convert comma seperated string into list - config parser cannot deal with lists
            config_post_rule_list = config_post_rule.split(",")

            # if new site name exists in config list then increment number
            while add_post_rule in config_post_rule_list:

                post_rule += 1
                add_post_rule = "rule_%s" % (str(post_rule))

            # check to make sure rule doesnt already exist
            if add_post_rule not in config_post_rule_list:

                # append rule to config list
                config_post_rule_list.append(add_post_rule)

            else:

                return ConfigPost().index()

            # convert back to comma seperated list and set
            config_post_rule = ",".join(config_post_rule_list)
            config_instance.config_obj["post_processing"]["post_rule"] = config_post_rule

        else:

            # if config entry is empty then create first entry
            config_instance.config_obj["post_processing"]["post_rule"] = add_post_rule

        # write default values to config.ini
        config_instance.config_obj["post_processing"]["%s_dropdown1" % add_post_rule] = "select"
        config_instance.config_obj["post_processing"]["%s_dropdown2" % add_post_rule] = "select"
        config_instance.config_obj["post_processing"]["%s_dropdown3" % add_post_rule] = "select"
        config_instance.config_obj["post_processing"]["%s_textbox1" % add_post_rule] = ""
        config_instance.config_obj["post_processing"]["%s_textbox2" % add_post_rule] = ""

        config_instance.config_obj.write()

        raise cherrypy.HTTPRedirect(".")

    # save config post processing rule form
    @cherrypy.expose
    def edit_config_rule(self, **kwargs):

        # read values from webui
        edit_config_rule = kwargs["edit_config_rule2"]
        edit_config_dropdown1 = kwargs["edit_config_dropdown1"]
        edit_config_dropdown2 = kwargs["edit_config_dropdown2"]
        edit_config_dropdown3 = kwargs["edit_config_dropdown3"]
        edit_config_textbox1 = kwargs["edit_config_textbox1"]
        edit_config_textbox2 = kwargs["edit_config_textbox2"]

        # if dropdown3 set to delete then remove any entry in textbox2 (path)
        if edit_config_dropdown3 == "delete":

            # write values to config.ini
            config_instance.config_obj["post_processing"]["%s_dropdown1" % edit_config_rule] = edit_config_dropdown1
            config_instance.config_obj["post_processing"]["%s_dropdown2" % edit_config_rule] = edit_config_dropdown2
            config_instance.config_obj["post_processing"]["%s_dropdown3" % edit_config_rule] = edit_config_dropdown3
            config_instance.config_obj["post_processing"]["%s_textbox1" % edit_config_rule] = edit_config_textbox1
            config_instance.config_obj["post_processing"]["%s_textbox2" % edit_config_rule] = ""

        # if dropdown3 set to move then check to make sure textbox2 (path) is not empty and path exists before saving
        elif edit_config_dropdown3 == "move" and edit_config_textbox2 != "" and uni_to_byte(os.path.exists(edit_config_textbox2)):

            # write values to config.ini
            config_instance.config_obj["post_processing"]["%s_dropdown1" % edit_config_rule] = edit_config_dropdown1
            config_instance.config_obj["post_processing"]["%s_dropdown2" % edit_config_rule] = edit_config_dropdown2
            config_instance.config_obj["post_processing"]["%s_dropdown3" % edit_config_rule] = edit_config_dropdown3
            config_instance.config_obj["post_processing"]["%s_textbox1" % edit_config_rule] = edit_config_textbox1
            config_instance.config_obj["post_processing"]["%s_textbox2" % edit_config_rule] = edit_config_textbox2

        config_instance.config_obj.write()

        raise cherrypy.HTTPRedirect(".")

    # delete config post processing rule form
    @cherrypy.expose
    def delete_config_rule(self, **kwargs):

        # get existing rule list
        config_post_rule = config_instance.config_obj["post_processing"]["post_rule"]

        delete_config_rule = kwargs["delete_config_rule2"]

        if config_post_rule:

            # convert comma seperated string into list - config parser cannot deal with lists
            config_post_rule_list = config_post_rule.split(",")

            if delete_config_rule:

                # check to make sure rule does exist
                if delete_config_rule in config_post_rule_list:

                    # delete selected post processing rule from list
                    config_post_rule_list.remove(delete_config_rule)

                else:

                    return ConfigPost().index()

                # convert back to comma seperated list and set
                config_post_rule_str = ",".join(config_post_rule_list)
                config_instance.config_obj["post_processing"]["post_rule"] = config_post_rule_str

                # delete config entries for selected post processing rule
                del config_instance.config_obj["post_processing"]["%s_dropdown1" % delete_config_rule]
                del config_instance.config_obj["post_processing"]["%s_dropdown2" % delete_config_rule]
                del config_instance.config_obj["post_processing"]["%s_dropdown3" % delete_config_rule]
                del config_instance.config_obj["post_processing"]["%s_textbox1" % delete_config_rule]
                del config_instance.config_obj["post_processing"]["%s_textbox2" % delete_config_rule]

        config_instance.config_obj.write()

        raise cherrypy.HTTPRedirect(".")


class ConfigScheduling(object):

    # read config scheduling page
    @cherrypy.expose
    def index(self):

        global template

        template = Template(file=os.path.join(templates_dir, "config_scheduling.tmpl"))
        template.section_name = "Config Scheduling"

        # read values from config.ini
        template.skin_color = config_instance.config_obj["general"]["skin_color"]
        template.index_schedule_hour = int(config_instance.config_obj["general"]["index_schedule_hour"])
        template.index_schedule_minute = int(config_instance.config_obj["general"]["index_schedule_minute"])
        template.post_schedule_hour = int(config_instance.config_obj["general"]["post_schedule_hour"])
        template.post_schedule_minute = int(config_instance.config_obj["general"]["post_schedule_minute"])

        header()

        footer()

        return str(template)

    # save config scheduling form
    @cherrypy.expose
    def save_config_scheduling(self, **kwargs):

        # check minimum schedule of 30 mins
        if kwargs["index_schedule_hour2"] == "0" and kwargs["index_schedule_minute2"] < "30":

            kwargs["index_schedule_minute2"] = "30"

        # write values to config.ini
        config_instance.config_obj["general"]["index_schedule_hour"] = kwargs["index_schedule_hour2"]
        config_instance.config_obj["general"]["index_schedule_minute"] = kwargs["index_schedule_minute2"]
        config_instance.config_obj["general"]["post_schedule_hour"] = kwargs["post_schedule_hour2"]
        config_instance.config_obj["general"]["post_schedule_minute"] = kwargs["post_schedule_minute2"]

        config_instance.config_obj.write()

        raise cherrypy.HTTPRedirect(".")


class ConfigUsenet(object):

    # read config index page
    @cherrypy.expose
    def index(self):

        global template

        template = Template(file=os.path.join(templates_dir, "config_usenet.tmpl"))
        template.section_name = "Config Usenet"

        # create variable for templates to read config entries
        template.config_obj = config_instance.config_obj

        template.index_site = config_instance.config_obj["usenet"]["index_site"]
        template.skin_color = config_instance.config_obj["general"]["skin_color"]
        template.newznab_cat_list = ["all formats", "other", "divx/xvid", "hd/x264", "foreign"]

        if template.index_site:

            # convert comma seperated string into list - config parser cannot deal with lists
            template.index_site_list = [x.strip() for x in template.index_site.split(",")]

        else:

            template.index_site_list = []

        # define list of supported newznab index sites
        template.add_index_site_list = ["nzbs.org", "nzb.su", "dognzb.cr", "nzbs4u.net", "nzb.ag", "usenet-crawler.com", "nmatrix.co.za", "newzb.net", "nzbplanet.net", "nzbndx.com", "nzbid.org", "custom"]

        header()

        footer()

        return str(template)

    # add config index form
    @cherrypy.expose
    def add_config_usenet(self, **kwargs):

        config_index_site = config_instance.config_obj["usenet"]["index_site"]
        add_newznab_site = kwargs["add_newznab_site2"]

        site_index = 1
        add_newznab_site_index = "%s-%s" % (add_newznab_site, str(site_index))

        if config_index_site:

            # convert comma seperated string into list - config parser cannot deal with lists
            config_index_site_list = config_index_site.split(",")

            # if new site name exists in config list then increment number
            while add_newznab_site_index in config_index_site_list:

                site_index += 1
                add_newznab_site_index = "%s-%s" % (add_newznab_site, str(site_index))

            # check to make sure rule doesnt already exist
            if add_newznab_site_index not in config_index_site_list:

                # append newznab site to config list
                config_index_site_list.append(add_newznab_site_index)

            else:

                return ConfigUsenet().index()

            # convert back to comma seperated list and set
            config_newznab_site = ",".join(config_index_site_list)
            config_instance.config_obj["usenet"]["index_site"] = config_newznab_site

        else:

            # if config entry is empty then create first entry
            config_instance.config_obj["usenet"]["index_site"] = add_newznab_site_index

        # set hostname, path, and port number for known index sites
        if add_newznab_site == "nzbs.org":

            config_instance.config_obj["usenet"]["%s_hostname" % add_newznab_site_index] = "https://nzbs.org"
            config_instance.config_obj["usenet"]["%s_portnumber" % add_newznab_site_index] = "443"

        elif add_newznab_site == "nzb.su":

            config_instance.config_obj["usenet"]["%s_hostname" % add_newznab_site_index] = "https://api.nzb.su"
            config_instance.config_obj["usenet"]["%s_portnumber" % add_newznab_site_index] = "443"

        elif add_newznab_site == "dognzb.cr":

            config_instance.config_obj["usenet"]["%s_hostname" % add_newznab_site_index] = "https://api.dognzb.cr"
            config_instance.config_obj["usenet"]["%s_portnumber" % add_newznab_site_index] = "443"

        elif add_newznab_site == "nzbs4u.net":

            config_instance.config_obj["usenet"]["%s_hostname" % add_newznab_site_index] = "https://nzbs4u.net"
            config_instance.config_obj["usenet"]["%s_portnumber" % add_newznab_site_index] = "443"

        elif add_newznab_site == "usenet-crawler.com":

            config_instance.config_obj["usenet"]["%s_hostname" % add_newznab_site_index] = "https://www.usenet-crawler.com"
            config_instance.config_obj["usenet"]["%s_portnumber" % add_newznab_site_index] = "443"

        elif add_newznab_site == "nzb.ag":

            config_instance.config_obj["usenet"]["%s_hostname" % add_newznab_site_index] = "https://nzb.ag"
            config_instance.config_obj["usenet"]["%s_portnumber" % add_newznab_site_index] = "443"

        elif add_newznab_site == "nmatrix.co.za":

            config_instance.config_obj["usenet"]["%s_hostname" % add_newznab_site_index] = "https://www.nmatrix.co.za"
            config_instance.config_obj["usenet"]["%s_portnumber" % add_newznab_site_index] = "443"

        elif add_newznab_site == "newzb.net":

            config_instance.config_obj["usenet"]["%s_hostname" % add_newznab_site_index] = "https://newzb.net"
            config_instance.config_obj["usenet"]["%s_portnumber" % add_newznab_site_index] = "443"

        elif add_newznab_site == "nzbplanet.net":

            config_instance.config_obj["usenet"]["%s_hostname" % add_newznab_site_index] = "https://nzbplanet.net"
            config_instance.config_obj["usenet"]["%s_portnumber" % add_newznab_site_index] = "443"

        elif add_newznab_site == "nzbndx.com":

            config_instance.config_obj["usenet"]["%s_hostname" % add_newznab_site_index] = "https://www.nzbndx.com"
            config_instance.config_obj["usenet"]["%s_portnumber" % add_newznab_site_index] = "443"

        elif add_newznab_site == "nzbid.org":

            config_instance.config_obj["usenet"]["%s_hostname" % add_newznab_site_index] = "http://nzbid.org"
            config_instance.config_obj["usenet"]["%s_portnumber" % add_newznab_site_index] = "80"

        else:

            config_instance.config_obj["usenet"]["%s_hostname" % add_newznab_site_index] = ""
            config_instance.config_obj["usenet"]["%s_portnumber" % add_newznab_site_index] = ""

        # write default values to config.ini
        config_instance.config_obj["usenet"]["%s_path" % add_newznab_site_index] = ""
        config_instance.config_obj["usenet"]["%s_key" % add_newznab_site_index] = ""
        config_instance.config_obj["usenet"]["%s_cat" % add_newznab_site_index] = ""
        config_instance.config_obj["usenet"]["%s_search_and" % add_newznab_site_index] = ""
        config_instance.config_obj["usenet"]["%s_search_or" % add_newznab_site_index] = ""
        config_instance.config_obj["usenet"]["%s_search_not" % add_newznab_site_index] = ""
        config_instance.config_obj["usenet"]["%s_minsize" % add_newznab_site_index] = "0"
        config_instance.config_obj["usenet"]["%s_maxsize" % add_newznab_site_index] = "0"
        config_instance.config_obj["usenet"]["%s_spotweb_support" % add_newznab_site_index] = "no"
        config_instance.config_obj["usenet"]["%s_enabled" % add_newznab_site_index] = "yes"

        config_instance.config_obj.write()

        raise cherrypy.HTTPRedirect(".")

    # save config index form
    @cherrypy.expose
    def edit_config_usenet(self, **kwargs):

        edit_newznab_site_index = kwargs["edit_newznab_site2"]

        # write values to config.ini
        config_instance.config_obj["usenet"]["%s_hostname" % edit_newznab_site_index] = kwargs["newznab_hostname2"]
        config_instance.config_obj["usenet"]["%s_path" % edit_newznab_site_index] = kwargs["newznab_path2"]
        config_instance.config_obj["usenet"]["%s_portnumber" % edit_newznab_site_index] = kwargs["newznab_portnumber2"]
        config_instance.config_obj["usenet"]["%s_key" % edit_newznab_site_index] = kwargs["newznab_key2"]
        config_instance.config_obj["usenet"]["%s_cat" % edit_newznab_site_index] = kwargs["newznab_cat2"]
        config_instance.config_obj["usenet"]["%s_search_and" % edit_newznab_site_index] = kwargs["newznab_search_and2"]
        config_instance.config_obj["usenet"]["%s_search_or" % edit_newznab_site_index] = kwargs["newznab_search_or2"]
        config_instance.config_obj["usenet"]["%s_search_not" % edit_newznab_site_index] = kwargs["newznab_search_not2"]
        config_instance.config_obj["usenet"]["%s_spotweb_support" % edit_newznab_site_index] = kwargs["spotweb_support2"]
        config_instance.config_obj["usenet"]["%s_enabled" % edit_newznab_site_index] = kwargs["newznab_enabled2"]

        if kwargs["newznab_minsize2"]:

            # check value is an integer, if not do not save
            try:
                int(kwargs["newznab_minsize2"])
                config_instance.config_obj["usenet"]["%s_minsize" % edit_newznab_site_index] = kwargs["newznab_minsize2"]

            except ValueError:

                pass

        else:

            config_instance.config_obj["usenet"]["%s_minsize" % edit_newznab_site_index] = "0"

        if kwargs["newznab_maxsize2"]:

            # check value is an integer, if not do not save
            try:
                int(kwargs["newznab_maxsize2"])
                config_instance.config_obj["usenet"]["%s_maxsize" % edit_newznab_site_index] = kwargs["newznab_maxsize2"]

            except ValueError:

                pass

        else:

            config_instance.config_obj["usenet"]["%s_maxsize" % edit_newznab_site_index] = "0"

        config_instance.config_obj.write()

        raise cherrypy.HTTPRedirect(".")

    # delete config index form
    @cherrypy.expose
    def delete_config_usenet(self, **kwargs):

        config_index_site = config_instance.config_obj["usenet"]["index_site"]
        delete_newznab_site_index = kwargs["delete_newznab_site2"]

        if config_index_site:

            # convert comma seperated string into list - config parser cannot deal with lists
            config_index_site_list = config_index_site.split(",")

            if delete_newznab_site_index:

                # delete selected index site from list
                config_index_site_list.remove(delete_newznab_site_index)
                delete_newznab_site = ",".join(config_index_site_list)
                config_instance.config_obj["usenet"]["index_site"] = delete_newznab_site

                # delete config entries for selected index site
                del config_instance.config_obj["usenet"]["%s_hostname" % delete_newznab_site_index]
                del config_instance.config_obj["usenet"]["%s_path" % delete_newznab_site_index]
                del config_instance.config_obj["usenet"]["%s_portnumber" % delete_newznab_site_index]
                del config_instance.config_obj["usenet"]["%s_key" % delete_newznab_site_index]
                del config_instance.config_obj["usenet"]["%s_cat" % delete_newznab_site_index]
                del config_instance.config_obj["usenet"]["%s_search_and" % delete_newznab_site_index]
                del config_instance.config_obj["usenet"]["%s_search_or" % delete_newznab_site_index]
                del config_instance.config_obj["usenet"]["%s_search_not" % delete_newznab_site_index]
                del config_instance.config_obj["usenet"]["%s_minsize" % delete_newznab_site_index]
                del config_instance.config_obj["usenet"]["%s_maxsize" % delete_newznab_site_index]
                del config_instance.config_obj["usenet"]["%s_spotweb_support" % delete_newznab_site_index]
                del config_instance.config_obj["usenet"]["%s_enabled" % delete_newznab_site_index]

        config_instance.config_obj.write()

        raise cherrypy.HTTPRedirect(".")


class ConfigTorrent(object):

    # read config index page
    @cherrypy.expose
    def index(self):

        global template

        template = Template(file=os.path.join(templates_dir, "config_torrent.tmpl"))
        template.section_name = "Config Torrent"

        # create variable for templates to read config entries
        template.config_obj = config_instance.config_obj

        template.index_site = config_instance.config_obj["torrent"]["index_site"]
        template.skin_color = config_instance.config_obj["general"]["skin_color"]

        if template.index_site:

            # convert comma seperated string into list - config parser cannot deal with lists
            template.index_site_list = [x.strip() for x in template.index_site.split(",")]

        else:

            template.index_site_list = []

        # define list of supported torrent index sites
        template.add_index_site_list = ["kickasstorrents", "torrentsapi", "piratebay", "bitsnoop", "rarbg", "demonoid", "monova", "torrenthound", "limetorrents"]

        header()

        footer()

        return str(template)

    # add config index form
    @cherrypy.expose
    def add_config_torrent(self, **kwargs):

        config_index_site = config_instance.config_obj["torrent"]["index_site"]
        add_torrent_site = kwargs["add_torrent_site2"]

        site_index = 1
        add_torrent_site_index = "%s-%s" % (add_torrent_site, str(site_index))

        if config_index_site:

            # convert comma seperated string into list - config parser cannot deal with lists
            config_index_site_list = config_index_site.split(",")

            # if new site name exists in config list then increment number
            while add_torrent_site_index in config_index_site_list:

                site_index += 1
                add_torrent_site_index = "%s-%s" % (add_torrent_site, str(site_index))

            # check to make sure rule doesnt already exist
            if add_torrent_site_index not in config_index_site_list:

                # append torrent site to config list
                config_index_site_list.append(add_torrent_site_index)

            else:

                return ConfigTorrent().index()

            # convert back to comma seperated list and set
            config_torrent_site = ",".join(config_index_site_list)
            config_instance.config_obj["torrent"]["index_site"] = config_torrent_site

        else:

            # if config entry is empty then create first entry
            config_instance.config_obj["torrent"]["index_site"] = add_torrent_site_index

        # set hostname, path, and port number for known index sites
        if add_torrent_site == "torrentsapi":
            config_instance.config_obj["torrent"]["%s_hostname" % add_torrent_site_index] = "https://api.torrentsapi.com"
            config_instance.config_obj["torrent"]["%s_portnumber" % add_torrent_site_index] = "443"

        # set hostname, path, and port number for known index sites
        if add_torrent_site == "bitsnoop":

            config_instance.config_obj["torrent"]["%s_hostname" % add_torrent_site_index] = "http://bitsnoop.com"
            config_instance.config_obj["torrent"]["%s_portnumber" % add_torrent_site_index] = "80"

        # set hostname, path, and port number for known index sites
        if add_torrent_site == "kickasstorrents":

            config_instance.config_obj["torrent"]["%s_hostname" % add_torrent_site_index] = "https://kat.cr"
            config_instance.config_obj["torrent"]["%s_portnumber" % add_torrent_site_index] = "443"

        # set hostname, path, and port number for known index sites
        if add_torrent_site == "piratebay":

            config_instance.config_obj["torrent"]["%s_hostname" % add_torrent_site_index] = "http://rss.thepiratebay.se"
            config_instance.config_obj["torrent"]["%s_portnumber" % add_torrent_site_index] = "80"

        # set hostname, path, and port number for known index sites
        if add_torrent_site == "demonoid":

            config_instance.config_obj["torrent"]["%s_hostname" % add_torrent_site_index] = "http://www.demonoid.pw"
            config_instance.config_obj["torrent"]["%s_portnumber" % add_torrent_site_index] = "80"

        # set hostname, path, and port number for known index sites
        if add_torrent_site == "rarbg":

            config_instance.config_obj["torrent"]["%s_hostname" % add_torrent_site_index] = "https://rarbg.to"
            config_instance.config_obj["torrent"]["%s_portnumber" % add_torrent_site_index] = "443"

        # set hostname, path, and port number for known index sites
        if add_torrent_site == "monova":

            config_instance.config_obj["torrent"]["%s_hostname" % add_torrent_site_index] = "http://monova.org"
            config_instance.config_obj["torrent"]["%s_portnumber" % add_torrent_site_index] = "80"

        # set hostname, path, and port number for known index sites
        if add_torrent_site == "torrenthound":

            config_instance.config_obj["torrent"]["%s_hostname" % add_torrent_site_index] = "http://www.torrenthound.com"
            config_instance.config_obj["torrent"]["%s_portnumber" % add_torrent_site_index] = "80"

        # set hostname, path, and port number for known index sites
        if add_torrent_site == "limetorrents":

            config_instance.config_obj["torrent"]["%s_hostname" % add_torrent_site_index] = "https://www.limetorrents.cc"
            config_instance.config_obj["torrent"]["%s_portnumber" % add_torrent_site_index] = "443"

        # write default values to config.ini
        config_instance.config_obj["torrent"]["%s_cat" % add_torrent_site_index] = ""
        config_instance.config_obj["torrent"]["%s_lang" % add_torrent_site_index] = ""
        config_instance.config_obj["torrent"]["%s_search_and" % add_torrent_site_index] = ""
        config_instance.config_obj["torrent"]["%s_search_or" % add_torrent_site_index] = ""
        config_instance.config_obj["torrent"]["%s_search_not" % add_torrent_site_index] = ""
        config_instance.config_obj["torrent"]["%s_minsize" % add_torrent_site_index] = "0"
        config_instance.config_obj["torrent"]["%s_maxsize" % add_torrent_site_index] = "0"
        config_instance.config_obj["torrent"]["%s_enabled" % add_torrent_site_index] = "yes"

        config_instance.config_obj.write()

        raise cherrypy.HTTPRedirect(".")

    # save config index form
    @cherrypy.expose
    def edit_config_torrent(self, **kwargs):

        edit_torrent_site_index = kwargs["edit_torrent_site2"]

        # write values to config.ini
        config_instance.config_obj["torrent"]["%s_hostname" % edit_torrent_site_index] = kwargs["torrent_hostname2"]
        config_instance.config_obj["torrent"]["%s_portnumber" % edit_torrent_site_index] = kwargs["torrent_portnumber2"]
        config_instance.config_obj["torrent"]["%s_cat" % edit_torrent_site_index] = kwargs["torrent_cat2"]
        config_instance.config_obj["torrent"]["%s_lang" % edit_torrent_site_index] = kwargs["torrent_lang2"]
        config_instance.config_obj["torrent"]["%s_search_and" % edit_torrent_site_index] = kwargs["torrent_search_and2"]
        config_instance.config_obj["torrent"]["%s_search_or" % edit_torrent_site_index] = kwargs["torrent_search_or2"]
        config_instance.config_obj["torrent"]["%s_search_not" % edit_torrent_site_index] = kwargs["torrent_search_not2"]
        config_instance.config_obj["torrent"]["%s_enabled" % edit_torrent_site_index] = kwargs["torrent_enabled2"]

        if kwargs["torrent_minsize2"]:

            # check value is an integer, if not do not save
            try:
                int(kwargs["torrent_minsize2"])
                config_instance.config_obj["torrent"]["%s_minsize" % edit_torrent_site_index] = kwargs["torrent_minsize2"]

            except ValueError:

                pass

        if kwargs["torrent_maxsize2"]:

            # check value is an integer, if not do not save
            try:
                int(kwargs["torrent_maxsize2"])
                config_instance.config_obj["torrent"]["%s_maxsize" % edit_torrent_site_index] = kwargs["torrent_maxsize2"]

            except ValueError:

                pass

        config_instance.config_obj.write()

        raise cherrypy.HTTPRedirect(".")

    # delete config index form
    @cherrypy.expose
    def delete_config_torrent(self, **kwargs):

        config_index_site = config_instance.config_obj["torrent"]["index_site"]
        delete_torrent_site_index = kwargs["delete_torrent_site2"]

        if config_index_site:

            # convert comma seperated string into list - config parser cannot deal with lists
            config_index_site_list = config_index_site.split(",")

            if delete_torrent_site_index:

                # delete selected index site from list
                config_index_site_list.remove(delete_torrent_site_index)
                delete_torrent_site = ",".join(config_index_site_list)
                config_instance.config_obj["torrent"]["index_site"] = delete_torrent_site

                # delete config entries for selected index site
                del config_instance.config_obj["torrent"]["%s_hostname" % delete_torrent_site_index]
                del config_instance.config_obj["torrent"]["%s_portnumber" % delete_torrent_site_index]
                del config_instance.config_obj["torrent"]["%s_cat" % delete_torrent_site_index]
                del config_instance.config_obj["torrent"]["%s_lang" % delete_torrent_site_index]
                del config_instance.config_obj["torrent"]["%s_search_and" % delete_torrent_site_index]
                del config_instance.config_obj["torrent"]["%s_search_or" % delete_torrent_site_index]
                del config_instance.config_obj["torrent"]["%s_search_not" % delete_torrent_site_index]
                del config_instance.config_obj["torrent"]["%s_minsize" % delete_torrent_site_index]
                del config_instance.config_obj["torrent"]["%s_maxsize" % delete_torrent_site_index]
                del config_instance.config_obj["torrent"]["%s_enabled" % delete_torrent_site_index]

        config_instance.config_obj.write()

        raise cherrypy.HTTPRedirect(".")


class ConfigDirectories(object):

    # read config directories page
    @cherrypy.expose
    def index(self):

        global template

        template = Template(file=os.path.join(templates_dir, "config_directories.tmpl"))
        template.section_name = "Config Folders"

        # read values from config.ini
        template.skin_color = config_instance.config_obj["general"]["skin_color"]
        template.movies_downloaded_dir = config_instance.config_obj["folders"]["movies_downloaded_dir"]
        template.movies_replace_dir = config_instance.config_obj["folders"]["movies_replace_dir"]
        template.usenet_watch_dir = config_instance.config_obj["folders"]["usenet_watch_dir"]
        template.usenet_archive_dir = config_instance.config_obj["folders"]["usenet_archive_dir"]
        template.usenet_completed_dir = config_instance.config_obj["folders"]["usenet_completed_dir"]
        template.torrent_watch_dir = config_instance.config_obj["folders"]["torrent_watch_dir"]
        template.torrent_archive_dir = config_instance.config_obj["folders"]["torrent_archive_dir"]
        template.torrent_completed_dir = config_instance.config_obj["folders"]["torrent_completed_dir"]
        template.certs_dir = config_instance.config_obj["folders"]["certs_dir"]
        template.results_dir = config_instance.config_obj["folders"]["results_dir"]
        template.logs_dir = config_instance.config_obj["folders"]["logs_dir"]
        header()

        footer()

        return str(template)

    # save config directories form
    @cherrypy.expose
    def save_config_directories(self, **kwargs):

        # write values to config.ini - check to see if folders exist and check folder logic
        if uni_to_byte(os.path.exists(kwargs["usenet_watch_dir2"])) and kwargs["usenet_watch_dir2"] != kwargs["usenet_archive_dir2"] or kwargs["usenet_watch_dir2"] == "":

            config_instance.config_obj["folders"]["usenet_watch_dir"] = del_inv_chars(kwargs["usenet_watch_dir2"])

        if uni_to_byte(os.path.exists(kwargs["usenet_archive_dir2"])) and kwargs["usenet_archive_dir2"] != kwargs["usenet_watch_dir2"] or kwargs["usenet_archive_dir2"] == "":

            config_instance.config_obj["folders"]["usenet_archive_dir"] = del_inv_chars(kwargs["usenet_archive_dir2"])

        if uni_to_byte(os.path.exists(kwargs["usenet_completed_dir2"])) or kwargs["usenet_completed_dir2"] == "":

            config_instance.config_obj["folders"]["usenet_completed_dir"] = del_inv_chars(kwargs["usenet_completed_dir2"])

        if uni_to_byte(os.path.exists(kwargs["torrent_watch_dir2"])) and kwargs["torrent_watch_dir2"] != kwargs["torrent_archive_dir2"] or kwargs["torrent_watch_dir2"] == "":

            config_instance.config_obj["folders"]["torrent_watch_dir"] = del_inv_chars(kwargs["torrent_watch_dir2"])

        if uni_to_byte(os.path.exists(kwargs["torrent_archive_dir2"])) and kwargs["torrent_archive_dir2"] != kwargs["torrent_watch_dir2"] or kwargs["torrent_archive_dir2"] == "":

            config_instance.config_obj["folders"]["torrent_archive_dir"] = del_inv_chars(kwargs["torrent_archive_dir2"])

        if uni_to_byte(os.path.exists(kwargs["torrent_completed_dir2"])) or kwargs["torrent_completed_dir2"] == "":

            config_instance.config_obj["folders"]["torrent_completed_dir"] = del_inv_chars(kwargs["torrent_completed_dir2"])

        if uni_to_byte(os.path.exists(kwargs["certs_dir2"])) and kwargs["certs_dir2"]:

            config_instance.config_obj["folders"]["certs_dir"] = del_inv_chars(kwargs["certs_dir2"])

        if uni_to_byte(os.path.exists(kwargs["results_dir2"])) and kwargs["results_dir2"]:

            config_instance.config_obj["folders"]["results_dir"] = del_inv_chars(kwargs["results_dir2"])

        if uni_to_byte(os.path.exists(kwargs["logs_dir2"])) and kwargs["logs_dir2"]:

            config_instance.config_obj["folders"]["logs_dir"] = del_inv_chars(kwargs["logs_dir2"])

        # create list of movies to replace
        movies_to_replace_list = (kwargs["movies_replace_dir2"]).split(",")

        exitcode = None

        for movies_to_replace_item in movies_to_replace_list:

            # if exist remove spaces at begning and end of string
            movies_to_replace_item = re.sub(ur"^\s+", "", movies_to_replace_item)
            movies_to_replace_item = re.sub(ur"\s+$", "", movies_to_replace_item)

            if uni_to_byte(os.path.exists(movies_to_replace_item)) and movies_to_replace_item != kwargs["usenet_completed_dir2"] and movies_to_replace_item != kwargs["torrent_completed_dir2"]:

                exitcode = 1

            else:

                exitcode = 0
                break

        # check exit codes and if positive then save changes
        if exitcode == 1 or kwargs["movies_replace_dir2"] == "":

            config_instance.config_obj["folders"]["movies_replace_dir"] = del_inv_chars(kwargs["movies_replace_dir2"])

        # create list of movies already downloaded
        movies_downloaded_list = (kwargs["movies_downloaded_dir2"]).split(",")

        for movies_downloaded_item in movies_downloaded_list:

            # if exist remove spaces at begning and end of string
            movies_downloaded_item = re.sub(ur"^\s+", "", movies_downloaded_item)
            movies_downloaded_item = re.sub(ur"\s+$", "", movies_downloaded_item)

            if uni_to_byte(os.path.exists(movies_downloaded_item)) and movies_downloaded_item != kwargs["usenet_completed_dir2"] and movies_downloaded_item != kwargs["torrent_completed_dir2"]:

                exitcode = 1

            else:

                exitcode = 0
                break

        # check exit codes and if positive then save changes
        if exitcode == 1 or kwargs["movies_downloaded_dir2"] == "":

            config_instance.config_obj["folders"]["movies_downloaded_dir"] = del_inv_chars(kwargs["movies_downloaded_dir2"])

        config_instance.config_obj.write()

        raise cherrypy.HTTPRedirect(".")


class ConfigNotification(object):

    # read config notification page
    @cherrypy.expose
    def index(self):

        global template

        template = Template(file=os.path.join(templates_dir, "config_notification.tmpl"))
        template.section_name = "Config Notification"

        # read values from config.ini
        template.skin_color = config_instance.config_obj["general"]["skin_color"]
        template.email_server = config_instance.config_obj["email_settings"]["email_server"]
        template.email_server_port = config_instance.config_obj["email_settings"]["email_server_port"]
        template.email_server_ssl = config_instance.config_obj["email_settings"]["email_server_ssl"]
        template.email_username = config_instance.config_obj["email_settings"]["email_username"]
        template.email_password = config_instance.config_obj["email_settings"]["email_password"]
        template.email_from = config_instance.config_obj["email_settings"]["email_from"]
        template.email_to = config_instance.config_obj["email_settings"]["email_to"]
        template.xbmc_host = config_instance.config_obj["xbmc"]["xbmc_host"]
        template.xbmc_port = config_instance.config_obj["xbmc"]["xbmc_port"]
        template.xbmc_username = config_instance.config_obj["xbmc"]["xbmc_username"]
        template.xbmc_password = config_instance.config_obj["xbmc"]["xbmc_password"]
        template.xbmc_notification = config_instance.config_obj["xbmc"]["xbmc_notification"]

        header()

        footer()

        return str(template)

    # save config email form
    @cherrypy.expose
    def save_config_notification(self, **kwargs):

        # write values to config.ini
        config_instance.config_obj["email_settings"]["email_server"] = kwargs["email_server2"]
        config_instance.config_obj["email_settings"]["email_server_port"] = kwargs["email_server_port2"]
        config_instance.config_obj["email_settings"]["email_server_ssl"] = kwargs["email_server_ssl2"]
        config_instance.config_obj["email_settings"]["email_username"] = kwargs["email_username2"]
        config_instance.config_obj["email_settings"]["email_password"] = kwargs["email_password2"]
        config_instance.config_obj["email_settings"]["email_from"] = kwargs["email_from2"]
        config_instance.config_obj["email_settings"]["email_to"] = kwargs["email_to2"]
        config_instance.config_obj["xbmc"]["xbmc_host"] = kwargs["xbmc_host2"]
        config_instance.config_obj["xbmc"]["xbmc_port"] = kwargs["xbmc_port2"]
        config_instance.config_obj["xbmc"]["xbmc_username"] = kwargs["xbmc_username2"]
        config_instance.config_obj["xbmc"]["xbmc_password"] = kwargs["xbmc_password2"]
        config_instance.config_obj["xbmc"]["xbmc_notification"] = kwargs["xbmc_notification2"]

        config_instance.config_obj.write()

        raise cherrypy.HTTPRedirect(".")

# webgui root folders
###


class ConfigRoot(object):

    # call class object for config subfolders
    scheduling = ConfigScheduling()
    notification = ConfigNotification()
    post = ConfigPost()
    directories = ConfigDirectories()
    usenet = ConfigUsenet()
    torrent = ConfigTorrent()
    imdb = ConfigIMDB()
    general = ConfigGeneral()
    switches = ConfigSwitches()

    # read config page
    @cherrypy.expose
    def index(self):

        global template

        template = Template(file=os.path.join(templates_dir, "config.tmpl"))
        template.section_name = "Config"

        # read values from config.ini
        template.skin_color = config_instance.config_obj["general"]["skin_color"]

        header()

        template.config_obj = config_instance.config_obj

        footer()

        return str(template)


class HistoryRoot(object):

    # display all records in database
    @cherrypy.expose
    def index(self, *args):

        global template

        template = Template(file=os.path.join(templates_dir, "history.tmpl"))
        template.section_name = "History"

        # read values from config.ini
        template.skin_color = config_instance.config_obj["general"]["skin_color"]
        template.enable_posters = config_instance.config_obj["switches"]["enable_posters"]
        template.history_sort_order = config_instance.config_obj["general"]["history_sort_order"]

        header()

        # get history search query result and row count from arguments passed from history_search
        history_search_query = args

        # check to see if search query result and row count exist, if not display all records in history
        if history_search_query != ():

            template.lines = history_search_query[0]
            template.sqlite_history_count = history_search_query[1]

        else:

            history_sort_order = config_instance.config_obj["general"]["history_sort_order"]
            history_max_items_shown = config_instance.config_obj["general"]["history_max_items_shown"]

            # select all rows from history table
            template.sqlite_history_count = sql_session.query(ResultsDBHistory).count()

            # remove scoped session
            sql_session.remove()

            # convert comma seperated sort order to list
            history_sort_order_list = [x.strip() for x in history_sort_order.split(",")]

            history_sort_order_scale = history_sort_order_list[0]
            history_sort_order_column = history_sort_order_list[1]

            # remove limit if max items shown is string all
            if history_max_items_shown == "all":

                history_max_items_shown = None

            # convert string asc desc to object
            if history_sort_order_scale == "asc":

                history_sort_order_scale = asc

            else:

                history_sort_order_scale = desc

            # contruct table and column name
            history_sort_order_column_attr = getattr(ResultsDBHistory, history_sort_order_column)

            # if history table column is type integer then do not do case insensitive sort order
            if history_sort_order_column == "imdbyear" or history_sort_order_column == "imdbruntime" or history_sort_order_column == "imdbvotes" or history_sort_order_column == "postdatesort" or history_sort_order_column == "postsizesort" or history_sort_order_column == "procdatesort":

                # select max items shown from history table with selected sort order
                template.lines = sql_session.query(ResultsDBHistory).order_by(history_sort_order_scale(history_sort_order_column_attr)).limit(history_max_items_shown)

                # remove scoped session
                sql_session.remove()

            else:

                # select max items shown from history table with selected sort order
                template.lines = sql_session.query(ResultsDBHistory).order_by(history_sort_order_scale(func.lower(history_sort_order_column_attr))).limit(history_max_items_shown)

                # remove scoped session
                sql_session.remove()

        footer()

        return str(template)

    # set sort order for history
    @cherrypy.expose
    def history_sort_order(self, **kwargs):

        config_instance.config_obj["general"]["history_sort_order"] = kwargs["sort_order"]

        # write settings to config.ini
        config_instance.config_obj.write()

        return HistoryRoot().index()

    # download nzb in thread to watched folder
    @cherrypy.expose
    def history_release(self, **kwargs):

        # if no items selected then return page, can happen if user clicks on the release selected button without selecting item
        if kwargs == {}:

            return HistoryRoot().index()

        history_id_item = kwargs["history_release_id"]

        # make sure queue id is not none, can happen if user forces downloads and then refreshes web interface
        if not history_id_item:

            return HistoryRoot().index()

        # if sqlite id is not list (single release) then convert to list, will be list if multiple items released using checkbox
        if type(history_id_item) is not list:

            history_id_item = [history_id_item]

        # send dictionary of id and table name to download function
        download_details_queue.put(dict(sqlite_table="history", sqlite_id=history_id_item))

        # loop over list of id's if multiple items selected
        for history_id_item in history_id_item:

            # select row from history table for selected id
            sqlite_history_row = sql_session.query(ResultsDBHistory).filter(ResultsDBHistory.id == history_id_item).first()

            # make sure id is in history table, can happen if user releases movie then clears history and refreshes
            if sqlite_history_row is not None:

                # update status for selected item to downloading for history table
                sqlite_history_row.dlstatus = "Downloading"

                # commit changes to db
                sql_session.commit()

            # remove scoped session
            sql_session.remove()

        # run download nzb/torrent thread
        DownloadThread().run()

        raise cherrypy.HTTPRedirect(".")

    # release all items in history
    @cherrypy.expose
    def history_release_all(self):

        # select id column in history table, result is list of tuples
        history_id_item = sql_session.query(ResultsDBHistory.id).all()

        # remove scoped session
        sql_session.remove()

        # remove tuple leaving list of id's using list comprehension
        history_id_item = [x[0] for x in history_id_item]

        # if sqlite id is not list (single release) then convert to list, will be list if multiple items released using checkbox
        if type(history_id_item) is not list:

            history_id_item = [history_id_item]

        # send sqlite id's to download queue
        download_details_queue.put(dict(sqlite_table="history", sqlite_id=history_id_item))

        # loop over list of id's if multiple items selected
        for history_id_item in history_id_item:

            # select row from history table for selected id
            sqlite_history_row = sql_session.query(ResultsDBHistory).filter(ResultsDBHistory.id == history_id_item).first()

            # make sure id is in history table, can happen if user releases movie then clears history and refreshes
            if sqlite_history_row is not None:

                # update status for selected item to downloading for history table
                sqlite_history_row.dlstatus = "Downloading"

                # commit changes to db
                sql_session.commit()

            # remove scoped session
            sql_session.remove()

        # run nzb download thread
        DownloadThread().run()

        raise cherrypy.HTTPRedirect(".")

    # delete selected items in history
    @cherrypy.expose
    def history_purge(self, **kwargs):

        # if no items selected then return page, can happen if user clicks on the release selected button without selecting item
        if kwargs == {}:

            return HistoryRoot().index()

        history_id_item = kwargs["history_purge_id"]

        # make sure history id is not none, can happen if user forces downloads and then refreshes web interface
        if not history_id_item:

            return HistoryRoot().index()

        # if sqlite id is not list (single release) then convert to list, will be list if multiple items released using checkbox
        if type(history_id_item) is not list:

            history_id_item = [history_id_item]

        # loop over list of id's if multiple items selected
        for history_id_item in history_id_item:

            # select row from history table for selected id
            sqlite_history_row = sql_session.query(ResultsDBHistory).filter(ResultsDBHistory.id == history_id_item)

            # make sure id is in history table, can happen if user releases movie then clears history and refreshes
            if sqlite_history_row is not None:

                # delete selected row in history table
                sqlite_history_row.delete()

                # commit changes to db
                sql_session.commit()

            # remove scoped session
            sql_session.remove()

        # shrink database
        sql_session.execute("VACUUM")

        # remove scoped session
        sql_session.remove()

        raise cherrypy.HTTPRedirect(".")

    # delete all items in history
    @cherrypy.expose
    def history_purge_all(self):

        # select imdbposter column in history table
        sqlite_imdbposter_history = sql_session.query(ResultsDBHistory.imdbposter).all()

        # remove scoped session
        sql_session.remove()

        # select imdbposter column in queued table
        sqlite_imdbposter_queued = sql_session.query(ResultsDBQueued.imdbposter).all()

        # remove scoped session
        sql_session.remove()

        # delete all data in hitory table
        sql_session.query(ResultsDBHistory).delete()
        sql_session.commit()

        # shrink database
        sql_session.execute("VACUUM")

        # remove scoped session
        sql_session.remove()

        # delete posters for rows deleted in history
        for sqlite_imdbposter_history_item in sqlite_imdbposter_history:

            # if imdbposter is not present in queued table, poster is not default.jpg, and file exists (maybe already deleted if duplicate entry in db) then delete
            if sqlite_imdbposter_history_item not in sqlite_imdbposter_queued and sqlite_imdbposter_history_item[0] != u"default.jpg" and uni_to_byte(os.path.exists(os.path.join(history_thumbnails_dir, sqlite_imdbposter_history_item[0]))):

                # delete poster image
                os.remove(os.path.join(history_thumbnails_dir, sqlite_imdbposter_history_item[0]))

        raise cherrypy.HTTPRedirect(".")

    # search history for imdb title
    @cherrypy.expose
    def history_search(self, **kwargs):

        # get submitted movie title
        history_search_title = kwargs["history_search_title"]

        # if history search title keyword is not empty then query sqlite for imdb movie title
        if history_search_title:

            # generate query to do partial match on imdbname with sort order set to imdbname asc
            history_search_query = sql_session.query(ResultsDBHistory).filter(ResultsDBHistory.imdbname.like(u'%' + history_search_title + u'%')).order_by(asc('imdbname'))

            # get list of all results found
            history_search_result = history_search_query.all()

            # count rows from all results found
            history_search_count = history_search_query.count()

            # remove scoped session
            sql_session.remove()

            # send history search result and row count to history index
            return HistoryRoot().index(history_search_result, history_search_count)

        else:

            return HistoryRoot().index()


class QueueRoot(object):

    # read files page
    @cherrypy.expose
    def index(self, *args):

        global template

        template = Template(file=os.path.join(templates_dir, "queue.tmpl"))
        template.section_name = "View Queue"

        # read values from config.ini
        template.skin_color = config_instance.config_obj["general"]["skin_color"]
        template.enable_posters = config_instance.config_obj["switches"]["enable_posters"]
        template.queue_sort_order = config_instance.config_obj["general"]["queue_sort_order"]

        header()

        # get queue search query result and row count from arguments passed from queue_search
        queue_search_query = args

        # check to see if search query result and row count exist, if not display all records in queue
        if queue_search_query != ():

            template.lines = queue_search_query[0]
            template.sqlite_queue_count = queue_search_query[1]

        else:

            queue_sort_order = config_instance.config_obj["general"]["queue_sort_order"]
            queue_max_items_shown = config_instance.config_obj["general"]["queue_max_items_shown"]

            # remove limit if max items shown is string all
            if queue_max_items_shown == "all":

                queue_max_items_shown = None

            # select all rows from queued table
            template.sqlite_queue_count = sql_session.query(ResultsDBQueued).count()

            # remove scoped session
            sql_session.remove()

            # convert comma seperated sort order to list
            queue_sort_order_list = [x.strip() for x in queue_sort_order.split(",")]

            queue_sort_order_scale = queue_sort_order_list[0]
            queue_sort_order_column = queue_sort_order_list[1]

            # remove limit if max items shown is string all
            if queue_max_items_shown == "all":

                queue_max_items_shown = None

            # convert string asc desc to object
            if queue_sort_order_scale == "asc":

                queue_sort_order_scale = asc

            else:

                queue_sort_order_scale = desc

            # contruct table and column name
            queue_sort_order_column_attr = getattr(ResultsDBQueued, queue_sort_order_column)

            # if queued table column is type integer then do not do case insensitive sort order
            if queue_sort_order_column == "postsizesort" or queue_sort_order_column == "imdbruntime" or queue_sort_order_column == "imdbvotes" or queue_sort_order_column == "postdatesort" or queue_sort_order_column == "postsizesort" or queue_sort_order_column == "procdatesort":

                # select max items shown from queued table with selected sort order
                template.lines = sql_session.query(ResultsDBQueued).order_by(queue_sort_order_scale(queue_sort_order_column_attr)).limit(queue_max_items_shown)

                # remove scoped session
                sql_session.remove()

            else:

                # select max items shown from queued table with selected sort order
                template.lines = sql_session.query(ResultsDBQueued).order_by(queue_sort_order_scale(func.lower(queue_sort_order_column_attr))).limit(queue_max_items_shown)

                # remove scoped session
                sql_session.remove()

        footer()

        return str(template)

    # set sort order for queued
    @cherrypy.expose
    def queue_sort_order(self, **kwargs):

        config_instance.config_obj["general"]["queue_sort_order"] = kwargs["sort_order"]

        # write settings to config.ini
        config_instance.config_obj.write()

        return QueueRoot().index()

    # download nzb in thread to watched folder
    @cherrypy.expose
    def queue_release(self, **kwargs):

        # if no items selected then return page, can happen if user clicks on the release selected button without selecting item
        if kwargs == {}:

            return QueueRoot().index()

        queue_id_item = kwargs["queue_release_id"]

        # make sure queue id is not none, can happen if user forces downloads and then refreshes web interface
        if not queue_id_item:

            return QueueRoot().index()

        # if sqlite id is not list (single release) then convert to list, will be list if multiple items released using checkbox
        if type(queue_id_item) is not list:

            queue_id_item = [queue_id_item]

        # send dictionary of id and table name to download function
        download_details_queue.put(dict(sqlite_table="queued", sqlite_id=queue_id_item))

        # loop over list of id's if multiple items selected
        for queue_id_item in queue_id_item:

            # select row from queued table for selected id
            sqlite_queued_row = sql_session.query(ResultsDBQueued).filter(ResultsDBQueued.id == queue_id_item).first()

            # make sure id is in queued table, can happen if user releases movie then clears queue and refreshes
            if sqlite_queued_row is not None:

                # update status for selected item to downloading for queued table
                sqlite_queued_row.dlstatus = "Downloading"

                # commit changes to db
                sql_session.commit()

            # remove scoped session
            sql_session.remove()

        # run download nzb/torrent thread
        DownloadThread().run()

        raise cherrypy.HTTPRedirect(".")

    # release all items in queue
    @cherrypy.expose
    def queue_release_all(self):

        # select id column in queue table, result is list of tuples
        queue_id_item = sql_session.query(ResultsDBQueued.id).all()

        # remove scoped session
        sql_session.remove()

        # remove tuple leaving list of id's using list comprehension
        queue_id_item = [x[0] for x in queue_id_item]

        # if sqlite id is not list (single release) then convert to list, will be list if multiple items released using checkbox
        if type(queue_id_item) is not list:

            queue_id_item = [queue_id_item]

        # send sqlite id's to download queue
        download_details_queue.put(dict(sqlite_table="queued", sqlite_id=queue_id_item))

        # loop over list of id's if multiple items selected
        for queue_id_item in queue_id_item:

            # select row from queued table for selected id
            sqlite_queued_row = sql_session.query(ResultsDBQueued).filter(ResultsDBQueued.id == queue_id_item).first()

            # make sure id is in queued table, can happen if user releases movie then clears queue and refreshes
            if sqlite_queued_row is not None:

                # update status for selected item to downloading for queued table
                sqlite_queued_row.dlstatus = "Downloading"

                # commit changes to db
                sql_session.commit()

            # remove scoped session
            sql_session.remove()

        # run nzb download thread
        DownloadThread().run()

        raise cherrypy.HTTPRedirect(".")

    # delete selected items in queue
    @cherrypy.expose
    def queue_purge(self, **kwargs):

        # if no items selected then return page, can happen if user clicks on the release selected button without selecting item
        if kwargs == {}:

            return QueueRoot().index()

        queue_id_item = kwargs["queue_purge_id"]

        # make sure queue id is not none, can happen if user forces downloads and then refreshes web interface
        if not queue_id_item:

            return QueueRoot().index()

        # if sqlite id is not list (single release) then convert to list, will be list if multiple items released using checkbox
        if type(queue_id_item) is not list:

            queue_id_item = [queue_id_item]

        # loop over list of id's if multiple items selected
        for queue_id_item in queue_id_item:

            # select row from queued table for selected id
            sqlite_queued_row = sql_session.query(ResultsDBQueued).filter(ResultsDBQueued.id == queue_id_item)

            # make sure id is in queue table, can happen if user releases movie then clears queue and refreshes
            if sqlite_queued_row is not None:

                # delete selected row in queue table
                sqlite_queued_row.delete()

                # commit changes to db
                sql_session.commit()

            # remove scoped session
            sql_session.remove()

        # shrink database
        sql_session.execute("VACUUM")

        # remove scoped session
        sql_session.remove()

        raise cherrypy.HTTPRedirect(".")

    # delete all items in queue
    @cherrypy.expose
    def queue_purge_all(self):

        # delete all data in queued table
        sql_session.query(ResultsDBQueued).delete()
        sql_session.commit()

        # shrink database
        sql_session.execute("VACUUM")

        # remove scoped session
        sql_session.remove()

        raise cherrypy.HTTPRedirect(".")

    # search queue for imdb title
    @cherrypy.expose
    def queue_search(self, **kwargs):

        # get submitted movie title
        queue_search_title = kwargs["queue_search_title"]

        # if queue search title keyword is not empty then query sqlite for imdb movie title
        if queue_search_title:

            # generate query to do partial match on imdbname with sort order set to imdbname asc
            queue_search_query = sql_session.query(ResultsDBQueued).filter(ResultsDBQueued.imdbname.like(u'%' + queue_search_title + u'%')).order_by(asc('imdbname'))

            # get list of all results found
            queue_search_result = queue_search_query.all()

            # count rows from all results found
            queue_search_count = queue_search_query.count()

            # remove scoped session
            sql_session.remove()

            # send queue search result and row count to queue index
            return QueueRoot().index(queue_search_result, queue_search_count)

        else:

            return QueueRoot().index()


class HomeRoot(object):

    # call class object for home subfolders
    config = ConfigRoot()
    history = HistoryRoot()
    queue = QueueRoot()

    # shutdown command
    @cherrypy.expose
    def shutdown(self):

        yield "Initiating shutdown..."
        yield "<br>Shutdown complete, please close this window"

        cherrypy.engine.exit()

    # restart command
    @cherrypy.expose
    def restart(self):

        yield "Initiating restart..."
        yield "<br>Please wait 30 seconds and then press the back button."

        cherrypy.engine.restart()

    # run search index thread manually - not scheduled timer
    @cherrypy.expose
    def run_now(self):

        # run search index thread
        SearchIndexThread().checks()

        return HomeRoot().index()

    # read index page
    @cherrypy.expose
    def index(self):

        global template

        template = Template(file=os.path.join(templates_dir, "home.tmpl"))
        template.section_name = "Home"

        # read values from config.ini
        template.skin_color = config_instance.config_obj["general"]["skin_color"]
        template.usenet_index_site = config_instance.config_obj["usenet"]["index_site"]
        template.usenet_watch_dir = config_instance.config_obj["folders"]["usenet_watch_dir"]
        template.usenet_archive_dir = config_instance.config_obj["folders"]["usenet_archive_dir"]
        template.usenet_completed_dir = config_instance.config_obj["folders"]["usenet_completed_dir"]
        template.torrent_index_site = config_instance.config_obj["torrent"]["index_site"]
        template.torrent_watch_dir = config_instance.config_obj["folders"]["torrent_watch_dir"]
        template.torrent_archive_dir = config_instance.config_obj["folders"]["torrent_archive_dir"]
        template.torrent_completed_dir = config_instance.config_obj["folders"]["torrent_completed_dir"]
        template.good_rating = config_instance.config_obj["imdb"]["good_rating"]
        template.good_votes = config_instance.config_obj["imdb"]["good_votes"]

        header()

        footer()

        return str(template)


def start_webgui():

    # check if webui username and password specified, if exist enable authentication for webconfig_settings
    if config_instance.config_obj["webconfig"]["username"] and config_instance.config_obj["webconfig"]["password"]:

        auth = True

    else:

        auth = False

    # create credentials store in dictionary for cherrypy
    userpassdict = {config_instance.config_obj["webconfig"]["username"]: config_instance.config_obj["webconfig"]["password"]}
    checkpassword = cherrypy.lib.auth_basic.checkpassword_dict(userpassdict)

    # read settings from config.ini and create cherrypy webconfig
    webconfig_settings = {

        'global': {
            'server.environment': "production",
            'engine.autoreload.on': False,
            'engine.timeout_monitor.on': False,
            'server.socket_host': uni_to_byte(config_instance.config_obj["webconfig"]["address"]),
            'server.socket_port': int(config_instance.config_obj["webconfig"]["port"]),
            'tools.staticdir.root': os.path.dirname(os.path.abspath(sys.argv[0]))
        },

        '/stylesheets': {
            'tools.staticdir.on': True,
            'tools.staticdir.dir': os.path.normpath(r"interfaces/%s/templates/static/stylesheets" % skin_theme)
        },

        '/javascript': {
            'tools.staticdir.on': True,
            'tools.staticdir.dir': os.path.normpath(r"interfaces/%s/templates/static/javascript" % skin_theme)
        },

        '/fonts': {
            'tools.staticdir.on': True,
            'tools.staticdir.dir': os.path.normpath(r"interfaces/%s/templates/static/fonts" % skin_theme)
        },

        '/images': {
            'tools.staticdir.on': True,
            'tools.staticdir.dir': os.path.normpath(r"images")
        },

        '/favicon.ico': {
            'tools.staticfile.on': True,
            'tools.staticfile.filename': os.path.normpath(r"%s/images/icon/favicon.ico" % (os.path.dirname(os.path.abspath(sys.argv[0]))))
        },

        '/': {
            'tools.auth_basic.on': auth,
            'tools.auth_basic.realm': "MovieGrabber",
            'tools.auth_basic.checkpassword': checkpassword,
            'tools.secureheaders.on': True,
            'tools.sessions.on': True,
            'tools.sessions.secure': True,
            'tools.sessions.httponly': True,
            'tools.encode.on': True,
            'tools.encode.encoding': "utf-8",
            'tools.gzip.on': True
        }

    }

    # update cherrypy config from settings above
    cherrypy.config.update(webconfig_settings)

    try:
        # check cherrypy port to see if its available
        cherrypy.process.servers.check_port(config_instance.config_obj["webconfig"]["address"], int(config_instance.config_obj["webconfig"]["port"]), timeout=1.0)

    except IOError:

        # if port not available print and log message and exit
        mg_log.warning(u"CherryPy failed to start on port %i, port already in use" % (int(config_instance.config_obj["webconfig"]["port"])))

        # assume moviegrabber already running, start clients default browser
        launch_default_browser()
        sys.exit(1)

    # quickstart cherrypy using python defined cherrypy config
    cherrypy.quickstart(HomeRoot(), config=webconfig_settings)


# download thread class
class DownloadThread(object):

    def run(self):

        # define download thread
        download_thread = threading.Thread(name="download_thread", target=Download().download_read, args=())
        download_thread.daemon = True
        download_thread.start()


# post processing thread class
class PostProcessingThread(object):

    def checks(self):

        self.enable_post_processing = config_instance.config_obj["switches"]["enable_post_processing"]

        if self.enable_post_processing == "yes":

            # enumerate list of running threads, includes daemonized and main process
            thread_list = threading.enumerate()

            # if thread NOT active then run
            if not any("post_processing_thread" in item.getName() for item in thread_list):

                self.run()

        # read scheduler from config.ini for post processing and convert to seconds
        post_processing_schedule_hour = int(config_instance.config_obj["general"]["post_schedule_hour"])
        post_processing_schedule_minute = int(config_instance.config_obj["general"]["post_schedule_minute"])
        post_processing_schedule_time = (post_processing_schedule_hour * 60) * 60 + (post_processing_schedule_minute * 60)

        # run post processing plugin as scheduled background task daemonized (non blocking)
        post_processing_schedule = threading.Timer(post_processing_schedule_time, self.checks)
        post_processing_schedule.daemon = True
        post_processing_schedule.start()

    def run(self):

        # start post processing thread
        post_processing_thread = threading.Thread(name="post_processing_thread", target=PostProcessing().run, args=())
        post_processing_thread.start()


# search index thread class
class SearchIndexThread(object):

    def __init__(self):

        self.index_site_item = None
        self.search_index_function = None
        self.download_method = None
        self.user_agent = None

    def checks(self):

        # get list of index sites defined in config,ini
        usenet_index_site = config_instance.config_obj["usenet"]["index_site"]
        torrent_index_site = config_instance.config_obj["torrent"]["index_site"]

        if usenet_index_site != "":

            # convert comma seperated string into list - config parser cannot deal with lists
            usenet_index_site_list = usenet_index_site.split(",")

            usenet_watch_dir = config_instance.config_obj["folders"]["usenet_watch_dir"]
            usenet_archive_dir = config_instance.config_obj["folders"]["usenet_archive_dir"]
            usenet_completed_dir = config_instance.config_obj["folders"]["usenet_completed_dir"]

            # loop over list of usenet index sites
            for usenet_index_site_item in usenet_index_site_list:

                config_index_enabled = config_instance.config_obj["usenet"]["%s_enabled" % usenet_index_site_item]

                if config_index_enabled == "yes":

                    self.index_site_item = usenet_index_site_item
                    self.search_index_function = "newznab_index"
                    self.download_method = "usenet"
                    self.user_agent = user_agent_moviegrabber

                    config_hostname = config_instance.config_obj["usenet"]["%s_hostname" % usenet_index_site_item]
                    config_portnumber = config_instance.config_obj["usenet"]["%s_portnumber" % usenet_index_site_item]
                    config_apikey = config_instance.config_obj["usenet"]["%s_key" % usenet_index_site_item]

                    # check all required details are complete for selected index sites
                    if config_hostname and config_portnumber and config_apikey and usenet_watch_dir and usenet_archive_dir and usenet_completed_dir:

                        self.run()

        if torrent_index_site != "":

            # convert comma seperated string into list - config parser cannot deal with lists
            torrent_index_site_list = torrent_index_site.split(",")

            torrent_watch_dir = config_instance.config_obj["folders"]["torrent_watch_dir"]
            torrent_archive_dir = config_instance.config_obj["folders"]["torrent_archive_dir"]
            torrent_completed_dir = config_instance.config_obj["folders"]["torrent_completed_dir"]

            # loop over list of torrent index sites
            for torrent_index_site_item in torrent_index_site_list:

                config_index_enabled = config_instance.config_obj["torrent"]["%s_enabled" % torrent_index_site_item]

                if config_index_enabled == "yes":

                    if "bitsnoop" in torrent_index_site_item:

                        self.index_site_item = torrent_index_site_item
                        self.search_index_function = "bitsnoop_index"
                        self.download_method = "torrent"
                        self.user_agent = user_agent_chrome

                        if torrent_watch_dir and torrent_archive_dir and torrent_completed_dir:

                            self.run()

                    if "torrentsapi" in torrent_index_site_item:

                        self.index_site_item = torrent_index_site_item
                        self.search_index_function = "torrentsapi_index"
                        self.download_method = "torrent"
                        self.user_agent = user_agent_chrome

                        if torrent_watch_dir and torrent_archive_dir and torrent_completed_dir:

                            self.run()

                    if "kickasstorrents" in torrent_index_site_item:

                        self.index_site_item = torrent_index_site_item
                        self.search_index_function = "kickasstorrents_index"
                        self.download_method = "torrent"
                        self.user_agent = user_agent_chrome

                        if torrent_watch_dir and torrent_archive_dir and torrent_completed_dir:

                            self.run()

                    if "piratebay" in torrent_index_site_item:

                        self.index_site_item = torrent_index_site_item
                        self.search_index_function = "piratebay_index"
                        self.download_method = "torrent"
                        self.user_agent = user_agent_moviegrabber

                        if torrent_watch_dir and torrent_archive_dir and torrent_completed_dir:

                            self.run()

                    if "demonoid" in torrent_index_site_item:

                        self.index_site_item = torrent_index_site_item
                        self.search_index_function = "demonoid_index"
                        self.download_method = "torrent"
                        self.user_agent = user_agent_chrome

                        if torrent_watch_dir and torrent_archive_dir and torrent_completed_dir:

                            self.run()

                    if "rarbg" in torrent_index_site_item:

                        self.index_site_item = torrent_index_site_item
                        self.search_index_function = "rarbg_index"
                        self.download_method = "torrent"
                        self.user_agent = user_agent_moviegrabber

                        if torrent_watch_dir and torrent_archive_dir and torrent_completed_dir:

                            self.run()

                    if "monova" in torrent_index_site_item:

                        self.index_site_item = torrent_index_site_item
                        self.search_index_function = "monova_index"
                        self.download_method = "torrent"
                        self.user_agent = user_agent_moviegrabber

                        if torrent_watch_dir and torrent_archive_dir and torrent_completed_dir:

                            self.run()

                    if "torrenthound" in torrent_index_site_item:

                        self.index_site_item = torrent_index_site_item
                        self.search_index_function = "torrenthound_index"
                        self.download_method = "torrent"
                        self.user_agent = user_agent_chrome

                        if torrent_watch_dir and torrent_archive_dir and torrent_completed_dir:

                            self.run()

                    if "limetorrents" in torrent_index_site_item:

                        self.index_site_item = torrent_index_site_item
                        self.search_index_function = "limetorrents_index"
                        self.download_method = "torrent"
                        self.user_agent = user_agent_chrome

                        if torrent_watch_dir and torrent_archive_dir and torrent_completed_dir:

                            self.run()

        # read scheduler from config.ini for search index and convert to seconds
        search_index_schedule_hour = int(config_instance.config_obj["general"]["index_schedule_hour"])
        search_index_schedule_minute = int(config_instance.config_obj["general"]["index_schedule_minute"])
        search_index_schedule_time = (search_index_schedule_hour * 60) * 60 + (search_index_schedule_minute * 60)

        # run search index plugin as scheduled background task daemonized (non blocking)
        search_index_threading_timer = threading.Timer(search_index_schedule_time, self.checks)
        search_index_threading_timer.daemon = True
        search_index_threading_timer.start()

    def run(self):

        # contruct class and function name and pass to thread with arguments
        search_index_function = getattr(SearchIndex(self.download_method, self.index_site_item, self.user_agent), self.search_index_function)

        # start search index thread
        search_index_thread = threading.Thread(name="search_index_thread_%s" % self.index_site_item, target=search_index_function, args=())
        search_index_thread.start()

        # construct time format string
        time_format = "%d/%m/%Y %H:%M:%S"
        current_date_time_str = time.strftime(time_format, time.localtime())

        # set current date time for last run
        config_instance.config_obj["general"]["last_run"] = current_date_time_str

        # write settings to config.ini
        config_instance.config_obj.write()


# version check thread class
class VersionCheckThread(object):

    # check current version
    def checks(self):

        check_version = config_instance.config_obj["general"]["check_version"]

        if check_version != "off":

            # set time format
            time_format = "%d/%m/%Y %H:%M:%S"

            # construct time format string
            current_date_time_str = time.strftime(time_format, time.localtime())
            last_version_check_str = config_instance.config_obj["general"]["last_version_check"]

            # if version check never run then start check
            if last_version_check_str == "":

                # start version check function
                version_check_thread = threading.Thread(name="version_check_thread", target=self.run(), args=())
                version_check_thread.daemon = True
                version_check_thread.start()
                mg_log.info(u"Version check started")

            else:

                # convert string to time object
                last_version_check = datetime.datetime.strptime(last_version_check_str, time_format)
                current_date_time = datetime.datetime.strptime(current_date_time_str, time_format)

                # compare last check and current time
                time_difference = current_date_time - last_version_check

                time_period = None

                if check_version == "daily":

                    time_period = time_difference > datetime.timedelta(days=1)

                elif check_version == "weekly":

                    time_period = time_difference > datetime.timedelta(weeks=1)

                if time_period:

                    # start version check function
                    version_check_thread = threading.Thread(name="version_check_thread", target=self.run(), args=())
                    version_check_thread.daemon = True
                    version_check_thread.start()
                    mg_log.info(u"Version check started")

        # run version check as scheduled background task daemonized (non blocking) - runs every 30 minutes
        version_check_theading_timer = threading.Timer(1800, self.checks)
        version_check_theading_timer.daemon = True
        version_check_theading_timer.start()

    def run(self):

        # set time format
        time_format = "%d/%m/%Y %H:%M:%S"

        # construct time format string
        current_date_time_str = time.strftime(time_format, time.localtime())

        # check sourceforge webpage for version and download url
        sourceforge_url = "http://moviegrabber.sourceforge.net/moviegrabber/version/latest"

        try:

            # download webpage
            sourceforge_webpage = urllib2.urlopen(sourceforge_url, timeout=1.0).read()

        except Exception:

            # set remote version to None
            config_instance.config_obj["general"]["remote_version"] = None

            # write settings to config.ini
            config_instance.config_obj.write()

            mg_log.warning(u"Version check failed, could not download sourceforge webpage")
            return

        # get remote version string
        remote_version = sourceforge_webpage.splitlines()[0]

        try:

            # if lib folder exists then source code download url, else win32 binary download url
            if uni_to_byte(os.path.exists(os.path.join(moviegrabber_root_dir, u"lib"))):

                remote_download = sourceforge_webpage.splitlines()[1]

            else:

                remote_download = sourceforge_webpage.splitlines()[2]

        except IndexError:

            # set remote download to None
            config_instance.config_obj["general"]["remote_download"] = None

            # write settings to config.ini
            config_instance.config_obj.write()

            mg_log.warning(u"Version check failed, no string present in sourceforge webpage")
            return

        # set remote version
        config_instance.config_obj["general"]["remote_version"] = remote_version

        # set remote download url
        config_instance.config_obj["general"]["remote_download"] = remote_download

        # set last version check
        config_instance.config_obj["general"]["last_version_check"] = current_date_time_str

        mg_log.info(u"Version check succeeded")

        # write settings to config.ini
        config_instance.config_obj.write()

        mg_log.info(u"Version check stopped")


# cherrypy start and stop methods for downloader thread(s)
class CherrypyVersionPlugin(cherrypy.process.plugins.SimplePlugin):

    def start(self):

        # run version check on startup
        VersionCheckThread().checks()

        self.bus.log("Started version check plugin")

    # set priority so runs after daemonizer thread started (priority 65, lower runs first) required for when running moviegrabber -d
    start.priority = 200

    def stop(self):

        self.bus.log("Stopped version check plugin")

    # set priority so search index stops first as its reliant on sqlite, lower number means higher priority
    stop.priority = 200


# cherrypy start and stop methods for downloader thread(s)
class CherrypyDownloadPlugin(cherrypy.process.plugins.SimplePlugin):

    def start(self):

        self.bus.log("Started download plugin")

    # set priority so runs after daemonizer thread started (priority 65, lower runs first) required for when running moviegrabber -d
    start.priority = 90

    def stop(self):

        # enumerate list of running threads, includes daemonized and main process
        thread_list = threading.enumerate()

        # if thread active then send poison pill
        if any("download_thread" in item.getName() for item in thread_list):

            # send poison pill to thread
            download_poison_queue.put("poison_pill")

            # wait for queue to join
            download_poison_queue.join()

        self.bus.log("Stopped download plugin")

    # set priority so search index stops first as its reliant on sqlite, lower number means higher priority
    stop.priority = 100


# cherrypy start and stop methods for post processinf thread(s)
class CherrypyPostPlugin(cherrypy.process.plugins.SimplePlugin):

    def start(self):

        # read scheduler from config.ini for post processing and convert to seconds
        post_processing_schedule_hour = int(config_instance.config_obj["general"]["post_schedule_hour"])
        post_processing_schedule_minute = int(config_instance.config_obj["general"]["post_schedule_minute"])
        post_processing_schedule_time = (post_processing_schedule_hour * 60) * 60 + (post_processing_schedule_minute * 60)

        # run post processing timer daemonized (non blocking)
        post_processing_threading_timer = threading.Timer(post_processing_schedule_time, PostProcessingThread().checks)
        post_processing_threading_timer.daemon = True
        post_processing_threading_timer.start()

        self.bus.log("Started post processing plugin")

    # set priority so runs after daemonizer thread started (priority 65, lower runs first) required for when running moviegrabber -d
    start.priority = 100

    def stop(self):

        # enumerate list of running threads, includes daemonized and main process
        thread_list = threading.enumerate()

        # if thread active then send poison pill
        if any("post_processing_thread" in item.getName() for item in thread_list):

            # send poison pill to thread
            search_index_poison_queue.put("poison_pill")

            # wait for queue to join
            search_index_poison_queue.join()

        self.bus.log("Stopped post processing plugin")

    # set priority so search index stops first as its reliant on sqlite, lower number means higher priority
    stop.priority = 90


# cherrypy start and stop methods for search index thread(s)
class CherrypySearchPlugin(cherrypy.process.plugins.SimplePlugin):

    def start(self):

        # read scheduler from config.ini for search index and convert to seconds
        search_index_schedule_hour = int(config_instance.config_obj["general"]["index_schedule_hour"])
        search_index_schedule_minute = int(config_instance.config_obj["general"]["index_schedule_minute"])
        search_index_schedule_time = (search_index_schedule_hour * 60) * 60 + (search_index_schedule_minute * 60)

        # run search index timer daemonized (non blocking)
        search_index_threading_timer = threading.Timer(search_index_schedule_time, SearchIndexThread().checks)
        search_index_threading_timer.daemon = True
        search_index_threading_timer.start()

        self.bus.log("Started search index plugin")

    # set priority so runs after daemonizer thread started (priority 65, lower runs first) required for when running moviegrabber daemonized
    start.priority = 110

    def stop(self):

        # enumerate list of running threads, includes daemonized and main process
        thread_list = threading.enumerate()

        # if thread active then send poison pill
        if any("search_index_thread" in item.getName() for item in thread_list):

            # send poison pill to thread
            search_index_poison_queue.put("poison_pill")

            # wait for queue to join
            search_index_poison_queue.join()

        self.bus.log("Stopped search index plugin")

    # set priority so search index stops first as its reliant on sqlite, lower number means higher priority
    stop.priority = 80


# secure cherrypy headers from attack
def secure_headers():

    headers = cherrypy.response.headers
    headers['X-Frame-Options'] = 'DENY'
    headers['X-XSS-Protection'] = '1; mode=block'
    headers['Content-Security-Policy'] = "default-src='self'"

    # if ssl enabled and defined then enable strict transport headers, age to 1 year
    if cherrypy.server.ssl_certificate is not None and cherrypy.server.ssl_private_key is not None:

        headers['Strict-Transport-Security'] = 'max-age=31536000'

cherrypy.tools.secureheaders = cherrypy.Tool('before_finalize', secure_headers, priority=60)


# run default client browser on startup
def launch_default_browser():

    mg_log.info(u"Launching browser")

    config_webconfig_address = config_instance.config_obj["webconfig"]["address"]
    config_webconfig_port = config_instance.config_obj["webconfig"]["port"]
    config_webconfig_enable_ssl = config_instance.config_obj["webconfig"]["enable_ssl"]

    # check if ssl is enabled
    if config_webconfig_enable_ssl == "yes":

        website_protocol = "https://"

    else:

        website_protocol = "http://"

    config_webconfig_address = re.sub(ur"\"", "", config_webconfig_address)

    # check for localhost
    if config_webconfig_address == "0.0.0.0":

        config_webconfig_address = "localhost"

    try:

        # open client browser
        webbrowser.open("%s%s:%s" % (website_protocol, config_webconfig_address, config_webconfig_port), 2, 1)

    except webbrowser.Error:

        try:

            # open client browser
            webbrowser.open("%s%s:%s" % (website_protocol, config_webconfig_address, config_webconfig_port), 1, 1)

        except webbrowser.Error:

            mg_log.warning(u"Cannot launch browser")

# required to prevent seperate process (search index) from trying to load parent process (webui)
if __name__ == '__main__':

    # read skin_theme name and pass to paths
    skin_theme = config_instance.config_obj["general"]["skin_theme"]

    # define path to cheetah templates
    templates_dir = os.path.join(moviegrabber_root_dir, u"interfaces/%s/templates" % skin_theme)
    templates_dir = os.path.normpath(templates_dir)

    # encode templates directory - required for cherrypy
    templates_dir = uni_to_byte(templates_dir)

    # define path to history thumbnail images
    history_thumbnails_dir = os.path.join(moviegrabber_root_dir, u"images/posters/thumbnails/history")
    history_thumbnails_dir = os.path.normpath(history_thumbnails_dir)

    # define path to queued thumbnail images
    queued_thumbnails_dir = os.path.join(moviegrabber_root_dir, u"images/posters/thumbnails/queued")
    queued_thumbnails_dir = os.path.normpath(queued_thumbnails_dir)

    # define path to static images
    images_dir = os.path.join(moviegrabber_root_dir, u"images")
    images_dir = os.path.normpath(images_dir)

    # create queue to send poison pill to post processing thread
    post_processing_poison_queue = Queue.Queue()

    # create queue to send poison pill to search index thread
    search_index_poison_queue = Queue.Queue()

    # create queue to send poison pill to download thread
    download_poison_queue = Queue.Queue()

    # create queue to send sqlite query result to download thread
    download_details_queue = Queue.Queue()

    # subscribe download plugin, plugin required to set priority level to allow daemonizer to run first
    download_plugin = CherrypyDownloadPlugin(cherrypy.engine)
    download_plugin.subscribe()

    # subscribe search index plugin, plugin required to set priority level to allow daemonizer to run first
    search_index_plugin = CherrypySearchPlugin(cherrypy.engine)
    search_index_plugin.subscribe()

    # subscribe post processing plugin, plugin required to set priority level to allow daemonizer to run first
    post_process_plugin = CherrypyPostPlugin(cherrypy.engine)
    post_process_plugin.subscribe()

    # subscribe version check plugin, plugin required to set priority level to allow daemonizer to run first
    version_check_plugin = CherrypyVersionPlugin(cherrypy.engine)
    version_check_plugin.subscribe()

    # check if ssl is enabled
    webui_ssl = config_instance.config_obj["webconfig"]["enable_ssl"]

    if webui_ssl == "yes":

        ssl_host_cert = os.path.join(config_instance.certs_dir, u"host.cert")
        ssl_host_key = os.path.join(config_instance.certs_dir, u"host.key")

        if os.path.exists(ssl_host_cert) and os.path.exists(ssl_host_key):

            try:

                # attempt to import openssl module, if error then disable ssl and log warning
                import OpenSSL

                # set path to ssl cert and key to enable strict transport headers
                cherrypy.server.ssl_certificate = ssl_host_cert
                cherrypy.server.ssl_private_key = ssl_host_key

            except ImportError:

                OpenSSL = None

                # if openssl not installed, disable ssl
                config_instance.config_obj["webconfig"]["enable_ssl"] = "no"

                # write settings to config.ini
                config_instance.config_obj.write()

                # set path to ssl cert and key to none to disable strict transport headers
                cherrypy.server.ssl_certificate = None
                cherrypy.server.ssl_private_key = None

                sys.stderr.write("WARNING - SSL disabled, you must install OpenSSL and pyOpenSSL to use HTTPS\n")

        else:

            # if path not found, disable ssl
            config_instance.config_obj["webconfig"]["enable_ssl"] = "no"

            # write settings to config.ini
            config_instance.config_obj.write()

            # set path to ssl cert and key to none to disable strict transport headers
            cherrypy.server.ssl_certificate = None
            cherrypy.server.ssl_private_key = None

            sys.stderr.write("WARNING - SSL disabled, certificate and key not found in specified folder %s\n" % (config_instance.certs_dir,))

    # run secure headers function
    secure_headers()

    # run sqlite check function
    sqlite_check()

    retry_count = 0

    # run assigned ip function
    while host_ip() == 0:

        time.sleep(5)
        retry_count += 1

        if retry_count == 6:

            sys.stderr.write("WARNING - No valid IPv4 address found after 30 seconds, please check host network config\n")
            sys.exit(1)

    # run valid config ip function
    config_ip()

    # check if launch browser on startup is enabled
    launch_browser = config_instance.config_obj["general"]["launch_browser"]

    if launch_browser == "yes":

        # hook browser launch into the cherrypy engine start
        cherrypy.engine.subscribe('start', launch_default_browser, priority=150)

        mg_log.info(u"Launch browser on startup enabled")

    # start cherrypy webui
    start_webgui()
