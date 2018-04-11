import asyncio
import aiohttp
from hbmqtt.client import MQTTClient, ClientException
from hbmqtt.mqtt.constants import QOS_1 as MQTT_QOS_1
import json
import logging
from aiohttp import web
import async_timeout

# import sys
# argv = sys.argv
# if len(argv) < 2:
#     sys.exit()

MQTT_SERVER = 'ha.ifts.ml/mqtt'
MQTT_USER = 'ha_user'
MQTT_PASS = 'client'
MQTT_PROTOCOL = 'wss'

MSG_PASS = b'encrypt+key+in+base64/=='
API_HEADERS = { 'x-ha-access': 'test' }
MQTT_TOPIC = 'httptest1'
HTTP_PREFIX = 'http://127.0.0.1:8123/'


logger = logging.getLogger(__name__)


from Crypto.Cipher import AES
from Crypto.Random import get_random_bytes
import zlib

import base64
real_key = base64.b64decode(MSG_PASS)

async def decrypt(data):
    if len(data) < 34:
        return None
    nonce = data[0:16]
    tag = data[16:32]
    encdata = data[32:]
    encobj = AES.new(real_key, AES.MODE_GCM, nonce=nonce)
    try:
        plaindata = encobj.decrypt_and_verify(encdata, tag)
    except ValueError:
        return None
    zobj = zlib.decompressobj(wbits=-15)
    result = zobj.decompress(plaindata) + zobj.flush()

    return result

async def encrypt(data):
    zobj = zlib.compressobj(wbits=-15)
    compressed = zobj.compress(data) + zobj.flush()
    encobj = AES.new(real_key, AES.MODE_GCM)

    encdata, tag = encobj.encrypt_and_digest(compressed)

    return encobj.nonce + tag + encdata


MQTT_TOPIC_REQ = 'req/' + MQTT_TOPIC
MQTT_TOPIC_RESP = 'resp/' + MQTT_TOPIC

mqtt_cfg = {
    'default_qos': 1,
    'keep_alive': 45,
    'reconnect_retries': 99,
}
mqtt = None

_G = {}

# Fix bug in HBMQTT when using websocket
async def _hbmqtt_hook_conn(self, *args):
    if hasattr(self.session, 'broker_uri_old'):
        self.session.broker_uri = self.session.broker_uri_old
    else:
        self.session.broker_uri_old = self.session.broker_uri
    return await self._old_connect_coro(*args)

MQTTClient._old_connect_coro = MQTTClient._connect_coro
MQTTClient._connect_coro = _hbmqtt_hook_conn

async def mqtt_init(topic):
    global mqtt
    if not mqtt:
        mqtt = MQTTClient(config=mqtt_cfg)
        await mqtt.connect(MQTT_PROTOCOL + '://' + MQTT_USER + ':' + MQTT_PASS + '@' + MQTT_SERVER)
    await mqtt.subscribe([(topic, MQTT_QOS_1)])

async def mqtt_publish(topic, data):
    await mqtt.publish(topic, data, MQTT_QOS_1)

async def mqtt_receive():
    return await mqtt.deliver_message()

async def http_process(method, url, headers, body):
    method_ = method.upper()
    url_ = HTTP_PREFIX + url
    async with _G['session'].request(method_, url_, headers=headers, data=body) as resp:
        respdata = await resp.text()
    return resp.status, respdata

async def proc_req(reqdata):
    if not reqdata:
        return
    obj = json.loads(reqdata.decode())
    code, resp = await http_process(
        obj.get('method'),
        obj.get('url'),
        obj.get('headers'),
        obj.get('body')
    )
    obj = {'callid': obj.get('callid'), 'code': code,'result': resp}
    data = await encrypt(json.dumps(obj).encode())
    await mqtt_publish(MQTT_TOPIC_RESP, data)

q_req = asyncio.Queue()

async def queue_loop():
    while True:
        try:
            pkt = (await mqtt_receive()).publish_packet
            topic = pkt.variable_header.topic_name
            if topic.startswith('req'):
                data = await decrypt(pkt.payload.data)
                if data is None:
                    continue
                await q_req.put(data)
            else:
                logger.warning('Topic not recongize: ' + topic)
        except Exception as e:
            logger.error(e)

async def main_client():
    _G['session'] = aiohttp.ClientSession(headers=API_HEADERS)
    await mqtt_init(MQTT_TOPIC_REQ)
    asyncio.ensure_future(queue_loop())
    while True:
        try:
            req = await q_req.get()
            asyncio.ensure_future(proc_req(req))
        except Exception as e:
            logger.error(e)


loop = asyncio.get_event_loop()

asyncio.ensure_future(main_client())
loop.run_forever()
loop.close()
