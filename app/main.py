import json
import base64
import logging
from threading import Lock
from datetime import datetime, timedelta

import boto3
import requests
from botocore.exceptions import ClientError
from flask import Flask, abort, jsonify, request
from flask_cors import CORS
from flask_marshmallow import Marshmallow
from blackfynn import Blackfynn
from app.config import Config

from app.serializer import ContactRequestSchema
from scripts.email_sender import EmailSender
from app.process_kb_results import *
# from pymongo import MongoClient

app = Flask(__name__)
# set environment variable
app.config["ENV"] = Config.DEPLOY_ENV

CORS(app)

ma = Marshmallow(app)
email_sender = EmailSender()
mongo = None
bf = None
s3 = boto3.client(
    "s3",
    aws_access_key_id=Config.SPARC_PORTAL_AWS_KEY,
    aws_secret_access_key=Config.SPARC_PORTAL_AWS_SECRET,
    region_name="us-east-1",
)

biolucida_lock = Lock()


class Biolucida(object):
    _token = ''
    _expiry_date = datetime.now() + timedelta(999999)
    _pending_authentication = False

    @staticmethod
    def set_token(value):
        Biolucida._token = value

    def token(self):
        return self._token

    @staticmethod
    def set_expiry_date(value):
        Biolucida._expiry_date = value

    def expiry_date(self):
        return self._expiry_date

    @staticmethod
    def set_pending_authentication(value):
        Biolucida._pending_authentication = value

    def pending_authentication(self):
        return self._pending_authentication


@app.errorhandler(404)
def resource_not_found(e):
    return jsonify(error=str(e)), 404

@app.before_first_request
def connect_to_blackfynn():
    global bf
    bf = Blackfynn(
        api_token=Config.BLACKFYNN_API_TOKEN,
        api_secret=Config.BLACKFYNN_API_SECRET,
        env_override=False,
        host=Config.BLACKFYNN_API_HOST
    )

# @app.before_first_request
# def connect_to_mongodb():
#     global mongo
#     mongo = MongoClient(Config.MONGODB_URI)


@app.route("/health")
def health():
    return json.dumps({"status": "healthy"})


@app.route("/contact", methods=["POST"])
def contact():
    data = json.loads(request.data)
    contact_request = ContactRequestSchema().load(data)

    name = contact_request["name"]
    email = contact_request["email"]
    message = contact_request["message"]

    email_sender.send_email(name, email, message)

    return json.dumps({"status": "sent"})


# Returns a list of embargoed (unpublished) datasets
# @api_blueprint.route('/datasets/embargo')
# def embargo():
#     collection = mongo[Config.MONGODB_NAME][Config.MONGODB_COLLECTION]
#     embargo_list = list(collection.find({}, {'_id':0}))
#     return json.dumps(embargo_list)


# Download a file from S3
@app.route("/download")
def create_presigned_url(expiration=3600):
    bucket_name = "blackfynn-discover-use1"
    key = request.args.get("key")
    response = s3.generate_presigned_url(
        "get_object",
        Params={"Bucket": bucket_name, "Key": key, "RequestPayer": "requester"},
        ExpiresIn=expiration,
    )

    return response


# Reverse proxy for objects from S3, a simple get object
# operation. This is used by scaffoldvuer and its 
# important to keep the relative <path> for accessing
# other required files.
@app.route("/s3-resource/<path:path>")
def direct_download_url(path):
    bucket_name = "blackfynn-discover-use1"

    head_response = s3.head_object(
        Bucket=bucket_name,
        Key=path,
        RequestPayer="requester",
    )

    content_length = head_response.get('ContentLength', None)
    if content_length and content_length > 20971520:  # 20 MB
        return abort(413, description=f"File too big to download: {content_length}")

    response = s3.get_object(
        Bucket=bucket_name,
        Key=path,
        RequestPayer="requester",
    )
    resource = response["Body"].read()
    return resource


@app.route("/sim/dataset/<id>")
def sim_dataset(id):
    if request.method == "GET":
        req = requests.get("{}/datasets/{}".format(Config.DISCOVER_API_HOST, id))
        json = req.json()
        inject_markdown(json)
        inject_template_data(json)
        return jsonify(json)


# /search/: Returns scicrunch results for a given <search> query
@app.route("/search/", defaults={'query': ''})
@app.route("/search/<query>")
def kb_search(query):
    try:
        response = requests.get(f'{Config.SCI_CRUNCH_HOST}/_search?q={query}&api_key={Config.KNOWLEDGEBASE_KEY}')
        return jsonify(process_kb_results(response.json())), 200
    except json.JSONDecodeError:
        return jsonify({'message': 'Could not parse Scicrunch output, please try again later',
                        'error': 'JSONDecodeError'}), 502
    except:
        return jsonify({'message': 'unexpeceted error','error': 'unkown'}), 500


# /filter-search/: Returns scicrunch results with optional params for facet filtering, sizing, and pagination
@app.route("/filter-search/", defaults={'query': ''})
@app.route("/filter-search/<query>/")
def filter_search(query):
    terms = request.args.getlist('term')
    facets = request.args.getlist('facet')
    size = request.args.get('size')
    start = request.args.get('start')

    # Create request
    data = create_filter_request(query, terms, facets, size, start)

    # Send request to scicrunch
    try:
        response = requests.post(
            f'{Config.SCI_CRUNCH_HOST}/_search?api_key={Config.KNOWLEDGEBASE_KEY}',
            json=data)
        results = process_kb_results(response.json())
    except requests.exceptions.HTTPError as err:
        logging.error(err)
        return jsonify({'error': str(err), 'message': 'Scicrunch is not currently reachable, please try again later'}), 502
    except json.JSONDecodeError as e:
        return jsonify({'message': 'Could not parse Scicrunch output, please try again later',
                        'error': 'JSONDecodeError'}), 502
    return jsonify(results), 200


# /get-facets/: Returns available scicrunch facets for filtering over given a <type> ('species', 'gender' etc)
@app.route("/interlax/<term>")
def ilx(term):
    data = {
    "size": 20,
    "from": 0,
    "query": {
        "bool": {
            "must": [ {
                "match_phrase": {
                    "label": {
                        "query": term
                    }
                }
            },
            {
                "term": {
                    "type": {
                        "value": "term"
                    }
                }
            } ]
        }
    },
    "_source": ["label","existing_ids.curie"]
}

    # Send request to scicrunch
    try:
        response = requests.post(
            f'{Config.ILX_HOST}/_search?api_key={Config.KNOWLEDGEBASE_KEY}',
            json=data)
        results = process_ilx( response.json())
    except requests.exceptions.HTTPError as err:
        logging.error(err)
        return jsonify(
            {'error': str(err), 'message': 'Scicrunch is not currently reachable, please try again later'}), 502
    except json.JSONDecodeError as e:
        return jsonify({'message': 'Could not parse Scicrunch output, please try again later',
                        'error': 'JSONDecodeError'}), 502
    return jsonify(results), 200

# /get-facets/: Returns available scicrunch facets for filtering over given a <type> ('species', 'gender' etc)
@app.route("/get-facets/<type>")
def get_facets(type):

    # Create facet query
    type_map, data = create_facet_query(type)

    # Make a request for each scicrunch parameter
    results = []
    for path in type_map[type]:
        data['aggregations'][f'{type}']['terms']['field'] = path
        response = requests.post(
            f'{Config.SCI_CRUNCH_HOST}/_search?api_key={Config.KNOWLEDGEBASE_KEY}',
            json=data)
        try:
            json_result = response.json()
            results.append(json_result)
        except BaseException as e:
            return jsonify({'message': 'Could not parse Scicrunch output, please try again later',
                            'error': 'JSONDecodeError'}), 502

    # Select terms from the results
    terms = []
    for result in results:
        terms += result['aggregations'][f'{type}']['buckets']

    return jsonify(terms), 200


def inject_markdown(resp):
    if "readme" in resp:
        mark_req = requests.get(resp.get("readme"))
        resp["markdown"] = mark_req.text


def inject_template_data(resp):
    id = resp.get("id")
    version = resp.get("version")
    if id is None or version is None:
        return

    try:
        response = s3.get_object(
            Bucket="blackfynn-discover-use1",
            Key="{}/{}/files/template.json".format(id, version),
            RequestPayer="requester",
        )
    except ClientError as e:
        # If the file is not under folder 'files', check under folder 'packages'
        logging.warning(
            "Required file template.json was not found under /files folder, trying under /packages..."
        )
        try:
            response = s3.get_object(
                Bucket="blackfynn-discover-use1",
                Key="{}/{}/packages/template.json".format(id, version),
                RequestPayer="requester",
            )
        except ClientError as e2:
            logging.error(e2)
            return

    template = response["Body"].read()

    try:
        template_json = json.loads(template)
    except ValueError as e:
        logging.error(e)
        return

    resp["study"] = {
        "uuid": template_json.get("uuid"),
        "name": template_json.get("name"),
        "description": template_json.get("description"),
    }


@app.route("/project/<project_id>", methods=["GET"])
def datasets_by_project_id(project_id):

    # 1 - call discover to get awards on all datasets (let put a very high limit to make sure we do not miss any)

    req = requests.get(
        "{}/search/records?limit=1000&offset=0&model=award".format(
            Config.DISCOVER_API_HOST
        )
    )

    json = req.json()["records"]

    # 2 - filter response to retain only awards with project_id
    result = filter(lambda x: "award_id" in x["properties"] and x["properties"]["award_id"] == project_id, json)

    ids = map(lambda x: str(x["datasetId"]), result)

    separator = "&ids="

    list_ids = separator.join(ids)

    # 3 - get the datasets from the list of ids from #2

    if len(list_ids) > 0:
        return requests.get(
            "{}/datasets?ids={}".format(Config.DISCOVER_API_HOST, list_ids)
        ).json()
    else:
        abort(404, description="Resource not found")

@app.route("/get_owner_email/<int:owner_id>", methods=["GET"])
def get_owner_email(owner_id):
    # Filter to find user based on provided int id
    org = bf._api._organization
    members = bf._api.organizations.get_members(org)
    res = [x for x in members if x.int_id == owner_id]

    if not res:
        abort(404, description="Owner not found")
    else:
        return jsonify({"email": res[0].email})

@app.route("/thumbnail/<image_id>", methods=["GET"])
def thumbnail_by_image_id(image_id, recursive_call=False):
    bl = Biolucida()

    with biolucida_lock:
        if not bl.token():
            authenticate_biolucida()

    url = Config.BIOLUCIDA_ENDPOINT + "/thumbnail/{0}".format(image_id)
    headers = {
        'token': bl.token(),
    }

    response = requests.request("GET", url, headers=headers)
    encoded_content = base64.b64encode(response.content)
    # Response from this endpoint is binary on success so the easiest thing to do is
    # check for an error response in encoded form.
    if encoded_content == b'eyJzdGF0dXMiOiJBZG1pbiB1c2VyIGF1dGhlbnRpY2F0aW9uIHJlcXVpcmVkIHRvIHZpZXcvZWRpdCB1c2VyIGluZm8uIFlvdSBtYXkgbmVlZCB0byBsb2cgb3V0IGFuZCBsb2cgYmFjayBpbiB0byByZXZlcmlmeSB5b3VyIGNyZWRlbnRpYWxzLiJ9'\
            and not recursive_call:
        # Authentication failure, try again after resetting token.
        with biolucida_lock:
            bl.set_token('')

        encoded_content = thumbnail_by_image_id(image_id, True)

    return encoded_content


@app.route("/image/<image_id>", methods=["GET"])
def image_info_by_image_id(image_id):
    url = Config.BIOLUCIDA_ENDPOINT + "/image/{0}".format(image_id)
    response = requests.request("GET", url)
    return response.json()


def authenticate_biolucida():
    bl = Biolucida()
    url = Config.BIOLUCIDA_ENDPOINT + "/authenticate"

    payload = {'username': Config.BIOLUCIDA_USERNAME,
               'password': Config.BIOLUCIDA_PASSWORD,
               'token': ''}
    files = [
    ]
    headers = {}

    response = requests.request("POST", url, headers=headers, data=payload, files=files)
    if response.status_code == requests.codes.ok:
        content = response.json()
        bl.set_token(content['token'])
