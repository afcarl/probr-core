import binascii
import json
import datetime
import dpkt
from bson import json_util
from captures.models import Capture
from utils.models import publishMessage
from probr import mongodb
from probr import socketioemitter
from probr.base_settings import WS4REDIS_CONNECTION
from probr import influx_config
from influxdb import InfluxDBClient
from influxdb.exceptions import InfluxDBClientError


__author__ = 'ale'

def generate_json(capture, packet, timestamp):
    tap = dpkt.radiotap.Radiotap(packet)
    t_len = binascii.hexlify(packet[2:3])    #t_len field indicates the entire length of the radiotap data, including the radiotap header.
    t_len = int(t_len,16)

    wlan = dpkt.ieee80211.IEEE80211(packet[t_len:])

    # todo: this can be extended to all necessary fields / data we need, done even on demand
    jsonPacket = {}
    jsonPacket['capture_uuid'] = capture.uuid

    if len(capture.tags.all()) > 0:
       jsonPacket['tags'] = list(capture.tags.names())

    jsonPacket['inserted_at'] = datetime.datetime.utcnow()
    jsonPacket['location'] = { 'type': 'Point', 'coordinates': [capture.longitude, capture.latitude] }

    jsonPacket['time'] = datetime.datetime.utcfromtimestamp(timestamp)

    try:
        jsonPacket['signal_strength'] = -(256-tap.ant_sig.db)
    except:
        pass
    try:
        jsonPacket['ssid'] = wlan.ies[0].info
    except:
        pass
    try:
        jsonPacket['mac_address_src'] = binascii.hexlify(wlan.mgmt.src)
    except:
        pass
    try:
        jsonPacket['mac_address_dst'] = binascii.hexlify(wlan.mgmt.dst)
    except:
        pass
    try:
        jsonPacket['squence_number'] = wlan.tcp.seq
    except:
        pass


    return jsonPacket

class MongoDBHandler(object):
    def handle(self, capture):
        if capture.file.size > 0:
            capture.file.open()
            pcapReader = dpkt.pcap.Reader(capture.file)

            for timestamp, packet in pcapReader:
                db = mongodb.db
                packets = db.packets
                jsonPacket = generate_json(capture, packet, timestamp)

                packets.insert_one(jsonPacket)

class WebsocketHandler(object):
    def handle(self, capture):
        if capture.file.size > 0:
            capture.file.open()
            pcapReader = dpkt.pcap.Reader(capture.file)

            for timestamp, packet in pcapReader:
                jsonPacket = generate_json(capture, packet, timestamp)

                # broadcast to socket
                jsonPacket["object_type"] = "packet:update"
                publishMessage("socket", message=json.dumps(jsonPacket, default=json_util.default))

class SocketIOHandler(object):
    def handle(self, capture):
        if capture.file.size > 0:
            capture.file.open()
            pcapReader = dpkt.pcap.Reader(capture.file)

            for timestamp, packet in pcapReader:
                jsonPacket = generate_json(capture, packet, timestamp)

                # broadcast to socket
                socketioemitter.io.Emit("packet:" + jsonPacket['mac_address_src'], json.dumps(jsonPacket, default=json_util.default))
                socketioemitter.io.Emit("packet:create", json.dumps(jsonPacket, default=json_util.default))

class InfluxDBHandler(object):

    def __init__(self):
        self.client = InfluxDBClient(influx_config.influx_host, influx_config.influx_port, influx_config.influx_user, influx_config.influx_pw, influx_config.influx_db)
        self.initDb(self.client)


    def handle(self, capture):
        if capture.file.size > 0:
            capture.file.open()
            pcapReader = dpkt.pcap.Reader(capture.file)

            for timestamp, packet in pcapReader:
                jsonPacket = generate_json(capture, packet, timestamp)
                points = self.restructure(jsonPacket)
                self.client.write_points(points)



    #check if db exists, if not create it, else do nothing
    def initDb(self,client):
        try:
            client.create_database(influx_config.influx_db)
        except InfluxDBClientError:
            return


    #We have to rewrite the packet structure since
    #for influxdb, the tags and fields in a packet must be in the form of:

    # packet = {
    #    tags : {
    #        tagname1: tagValue,
    #        tagname2: tagValue,
    #        .
    #        .
    #    },
    #    time: ..... ,
    #    fields : {
    #        ssid: ...,
    #        mac_addr_src:....,
    #        .
    #        .
    #        value: .....
    #    }
    # }


    def restructure(self, jsonPacket):
        new_packet = {}

        # rewrite the tags
        tags = jsonPacket["tags"]
        influx_tags = {}
        for tag in tags:
            influx_tags[tag]=tag

        # put tags in new packet
        new_packet["tags"] = influx_tags

        # rewrite fields
        fields = {}
        fields["capture_uuid"] = jsonPacket['capture_uuid']
        fields["signal_strength"] = jsonPacket['signal_strength']
        fields["ssid"] = jsonPacket['ssid']
        fields["mac_address_src"] = jsonPacket['mac_address_src']
        fields["mac_address_dst"] = jsonPacket['mac_address_dst']
        fields["inserted_at"] = str(jsonPacket['inserted_at'])
        fields["longitude"] = jsonPacket['location']["coordinates"][0]
        fields["latitude"] = jsonPacket['location']["coordinates"][1]

        # put fields in new packet
        new_packet["fields"] = fields

        # add necessary measurement field
        new_packet["measurement"] = "packet"

        # put the whole thing as a point in a list (required structure by influx)
        points = []
        points.append(new_packet)

        return points
