import binascii,io,struct,unittest,zipfile
from unittest.mock import patch
from scripts import gui_agentnet_sample as sample


class AgentNetRangeTests(unittest.TestCase):
    def archive(self):
        stream=io.BytesIO()
        with zipfile.ZipFile(stream,'w',compression=zipfile.ZIP_DEFLATED) as archive:archive.writestr('unsafe/../one.png',b'image data'*20)
        blob=stream.getvalue();footer=struct.unpack('<4s4H2LH',blob[-22:])
        records=sample.central_entries(blob[footer[6]:footer[6]+footer[5]])
        return blob,records['one.png'][0]

    def test_central_directory_and_bounded_payload_decode_match_zipfile(self):
        blob,record=self.archive();header=struct.unpack_from('<4s5H3L2H',blob,record['offset'])
        start=record['offset']+30+header[-2]+header[-1]
        self.assertEqual(sample.decode_member(record,blob[start:start+record['compressed']]),b'image data'*20)
        self.assertEqual(record['disk'],0)

    def test_crc_corruption_and_decompression_cap_are_rejected(self):
        blob,record=self.archive();header=struct.unpack_from('<4s5H3L2H',blob,record['offset']);start=record['offset']+30+header[-2]+header[-1]
        with self.assertRaises(ValueError):sample.decode_member(dict(record,crc=0),blob[start:start+record['compressed']])
        with self.assertRaises(ValueError):sample.decode_member(dict(record,size=17*1024**2),b'')

    def test_zip64_offset_is_not_truncated_to_32_bits(self):
        offset=5_000_000_000;name=b'image.png';extra=struct.pack('<HHQ',1,8,offset)
        header=struct.pack('<4s6H3L5H2L',b'PK\x01\x02',45,45,0,0,0,0,0,1,1,len(name),len(extra),0,3,0,0,0xffffffff)
        record=sample.central_entries(header+name+extra)['image.png'][0]
        self.assertEqual(record['offset'],offset);self.assertEqual(record['disk'],3)

    def test_read_spanning_volume_boundary_keeps_every_byte(self):
        class Fake(sample.Fetch):
            def get(self,name,start,length):
                disk=int(name.split('.z')[-1])-1
                return (b'abcdefgh' if disk==0 else b'ijklmnop')[start:start+length]
        with patch.object(sample,'PART',8):
            self.assertEqual(Fake(32).part(0,6,6),b'ghijkl')

    def test_invalid_central_record_is_rejected(self):
        with self.assertRaises(ValueError):sample.central_entries(b'X'*46)
