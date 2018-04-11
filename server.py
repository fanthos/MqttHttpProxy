import asyncio
import aiohttp
from hbmqtt.client import MQTTClient, ClientException
from hbmqtt.mqtt.constants import QOS_1 as MQTT_QOS_1
import json
import logging
from aiohttp import web
import async_timeout
from collections import namedtuple
import sqlite3
import base64

# import sys
# argv = sys.argv
# if len(argv) < 2:
#     sys.exit()

MQTT_SERVER = 'ha.ifts.ml/mqtt'
MQTT_USER = 'ha_srv'
MQTT_PASS = 'server'
MQTT_PROTOCOL = 'wss'

SERVER_PORT = 8180
CLIENT_TIMEOUT = 10

MSG_PASS_DICT = {
    'httptest1': b'key in 16 bytes!',
}


logger = logging.getLogger(__name__)

from Crypto.Cipher import AES
from Crypto.Random import get_random_bytes
import zlib

async def getcryptkey(clientid):
    ''' Get client key for client id. '''
    return await user_get(clientid)

async def decrypt(clientid, data):
    ''' Decrypt and decompress data. '''
    if len(data) < 34:
        return None
    nonce = data[0:16]
    tag = data[16:32]
    encdata = data[32:]
    msgpass = await getcryptkey(clientid)
    if msgpass is None:
        return None
    encobj = AES.new(msgpass, AES.MODE_GCM, nonce=nonce)
    try:
        plaindata = encobj.decrypt_and_verify(encdata, tag)
    except ValueError:
        return None
    zobj = zlib.decompressobj(wbits=-15)
    result = zobj.decompress(plaindata) + zobj.flush()

    return result

async def encrypt(clientid, data):
    ''' Encrypt and hash data. '''
    msgpass = await getcryptkey(clientid)
    if msgpass is None:
        return None
    zobj = zlib.compressobj(wbits=-15)
    compressed = zobj.compress(data) + zobj.flush()
    encobj = AES.new(msgpass, AES.MODE_GCM)

    encdata, tag = encobj.encrypt_and_digest(compressed)

    return encobj.nonce + tag + encdata

mqtt_cfg = {
    'default_qos': 1,
    'keep_alive': 45,
    'reconnect_retries': 99,
}
mqtt = None

_G = {}

async def user_init():
    conn = sqlite3.connect('users.db')
    _G['conn'] = conn

async def user_get(cid):
    conn = _G['conn']
    c = conn.cursor()
    r = c.execute('SELECT enckey FROM users WHERR cid=?', (cid,))
    b = r.fetchone()
    return base64.b64decode(b[0])

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

server_req_evt = [None]
server_req_max = 1
server_req_que = asyncio.Queue()

async def queue_process(topic, origdata):
    _, clientid = topic.split('/', maxsplit=1)
    data = await decrypt(clientid, origdata)
    if data is None:
        return
    obj = json.loads(data)
    callid = obj.get('callid')
    if callid is None:
        return
    evt = server_req_evt[callid]
    if evt is None or evt[1] != clientid:
        return
    evt[2] = obj['code']
    evt[3] = obj['result']
    evt[0].set()

async def queue_loop():
    while True:
        try:
            pkt = (await mqtt_receive()).publish_packet
            topic = pkt.variable_header.topic_name
            if topic.startswith('resp/'):
                asyncio.ensure_future(queue_process(topic, pkt.payload.data))
            else:
                logger.warning('Topic not recongize: ' + topic)
        except Exception as e:
            logger.error(e)

class GotoException(Exception): pass
async def server_handler(request):
    global server_req_max

    clientid = request.match_info['cid']
    url = request.match_info['api']
    data = await request.text()
    if server_req_que.qsize() <= server_req_max // 2:
        for i in range(server_req_max, server_req_max * 2):
            await server_req_que.put(i)
        server_req_max *= 2
        diff = server_req_max - len(server_req_evt)
        server_req_evt.extend([None] * diff)

    # _, clientid, url = request.path_qs.split('/', maxsplit=2)
    callid = await server_req_que.get()
    try:
        obj = {
            'callid': callid,
            'method': request.method,
            'url': url,
            'body': data,
        }
        evt = [asyncio.Event(), clientid, 0, '']
        server_req_evt[callid] = evt
        data = json.dumps(obj).encode()

        await mqtt_publish('req/' + clientid, await encrypt(clientid, data))
        async with async_timeout.timeout(CLIENT_TIMEOUT) as tm:
            await evt[0].wait()
        if tm.expired:
            raise asyncio.TimeoutError()

        ret = web.Response(status=evt[2], text=evt[3])
    except GotoException:
        pass
    except asyncio.TimeoutError:
        ret = web.Response(status=504, text='Client Timeout Exceed.')
    except Exception as e:
        logger.error(e)
        ret = web.Response(status=500, text='Unknown Error')
    finally:
        server_req_evt[callid] = None
        await server_req_que.put(callid)
    return ret

async def server_init():
    app = web.Application()
    app.router.add_route(aiohttp.hdrs.METH_ANY, '/{cid}/{api:.*}', server_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, port=SERVER_PORT)
    await site.start()
    # web.run_app(app, port=SERVER_PORT)
    return runner

async def main_server():
    await mqtt_init('resp/#')
    await user_init()
    await server_init()
    asyncio.ensure_future(queue_loop())

loop = asyncio.get_event_loop()

asyncio.ensure_future(main_server())
loop.run_forever()
loop.close()
