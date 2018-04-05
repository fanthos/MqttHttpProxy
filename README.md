# MqttHttpProxy
Access HTTP service behind NAT using public MQTT service.

The client will subscript to `req/<clientid>` and publish HTTP response to `resp/<clientid>`. The HTTP server will publish HTTP request data to `req/<clientid>` encrypted by AES-GCM with difference key for each clients. The client will make HTTP request using data published by server and publish encrypted response to `resp/<clientid>`.

Client configuration:
```python
MQTT_SERVER = 'mqtt://127.0.0.1/'
MQTT_USER = ''
MQTT_PASS = ''

MSG_PASS = b'key in 16 bytes!'
# AES key
API_HEADERS = { 'x-ha-access': 'test' }
# Headers, Home Assistant using this header to pass password
MQTT_TOPIC = 'httptest1'
# Client ID
HTTP_PREFIX = 'http://127.0.0.1:8123/api/'
# API prefix
```

Server configuration:
```python
MQTT_SERVER = 'mqtt://127.0.0.1/'
MQTT_USER = ''
MQTT_PASS = ''

SERVER_PORT = 8180
# HTTP Proxy listen port
CLIENT_TIMEOUT = 10
# Max wait time for Client response

MSG_PASS_DICT = {
    'httptest1': b'key in 16 bytes!',
}
# Dictionary of user list.
```

By modifing `encrypt` and `decrypt` in server, its very easy to add multi-user support using database.

Caller
-HTTP->
Proxy Server
-MQTT->
MQTT Server
-MQTT->
Proxy Client
-HTTP->
HTTP Server
