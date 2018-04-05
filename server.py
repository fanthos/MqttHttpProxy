import asyncio
import aiohttp
from hbmqtt.client import MQTTClient, ClientException
from hbmqtt.mqtt.constants import QOS_1 as MQTT_QOS_1
import json
import logging
from aiohttp import web
import async_timeout
from collections import namedtuple

# import sys
# argv = sys.argv
# if len(argv) < 2:
#     sys.exit()

MQTT_SERVER = 'mqtt://127.0.0.1/'
MQTT_USER = ''
MQTT_PASS = ''

SERVER_PORT = 8180
CLIENT_TIMEOUT = 10


logger = logging.getLogger(__name__)

from Crypto.Cipher import AES
from Crypto.Random import get_random_bytes

async def decrypt(clientid, data):
    return data

async def encrypt(clientid, data):
    return data

mqtt_cfg = {
    'default_qos': 1,
    'keep_alive': 45,
    'reconnect_retries': 99,
}
mqtt = None

_G = {}

async def mqtt_init(topic):
    global mqtt
    if not mqtt:
        mqtt = MQTTClient(config=mqtt_cfg)
        await mqtt.connect(MQTT_SERVER)
    await mqtt.subscribe([(topic, MQTT_QOS_1)])

async def mqtt_publish(topic, data):
    await mqtt.publish(topic, data, MQTT_QOS_1)

async def mqtt_receive():
    return await mqtt.deliver_message()

server_req_evt = [None]
server_req_max = 1
server_req_que = asyncio.Queue()

async def queue_loop():
    while True:
        try:
            pkt = (await mqtt_receive()).publish_packet
            topic = pkt.variable_header.topic_name
            if topic.startswith('resp/'):
                _, clientid = topic.split('/', maxsplit=1)
                data = await decrypt(clientid, pkt.payload.data)
                if data is None:
                    continue
                obj = json.loads(data)
                callid = obj.get('callid')
                if callid is None:
                    continue
                evt = server_req_evt[callid]
                if evt is None or evt[1] != clientid:
                    continue
                evt[2] = obj['code']
                evt[3] = obj['result']
                evt[0].set()
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
    await server_init()
    asyncio.ensure_future(queue_loop())

loop = asyncio.get_event_loop()
loop.set_debug(True)

asyncio.ensure_future(main_server())
loop.run_forever()
loop.close()
