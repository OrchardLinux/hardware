/* SPDX-License-Identifier: GPL-2.0-only
 * Whole-file, side-effect-free preflight for the J71b Z2FW stream.
 * Packet/word encoding follows the pinned Pauli1Go converter. No payload logging.
 */
#define J71B_FW_BYTES 265996
#define J71B_FW_RECORDS 12
struct j71b_fw_record {const u8 *data;u32 size;};
struct j71b_fw_plan {struct j71b_fw_record records[J71B_FW_RECORDS];};
static u32 fw_le32(const u8*p)
{return (u32)p[0]|((u32)p[1]<<8)|((u32)p[2]<<16)|((u32)p[3]<<24);}
static u32 fw_be16(const u8*p){return ((u32)p[0]<<8)|p[1];}
static u32 fw_xe32(const u8*p){return fw_be16(p)|(fw_be16(p+2)<<16);}
static int j71b_validate_fw(const u8*data,size_t size,struct j71b_fw_plan*plan)
{
 static const u32 commands[]={4,0,1,1,1,1,1,1,1,1,1,3};
 static const u32 lengths[]={44,4,229524,1296,144,5096,29716,16,16,16,16,4};
 static const u32 config[]={2559,16,125,10000,1,1,0,41,1,500,260};
 static const u32 addresses[]={0x100000,0x100400,0x102a00,0x101400,0x102e00};
 size_t pos=8;
 unsigned int i,j;
 if(size!=J71B_FW_BYTES || fw_le32(data)!=0x5746325a || fw_le32(data+4)!=1)
  return -EINVAL;
 for(i=0;i<J71B_FW_RECORDS;i++){
  const u8*p;
  u32 len,code,sum=0;
  size_t end,next;
  if(pos>size || size-pos<8)return -EINVAL;
  code=fw_le32(data+pos);len=fw_le32(data+pos+4);pos+=8;
  if(code!=commands[i] || len!=lengths[i] || len>size-pos)return -EINVAL;
  p=data+pos;plan->records[i]=(struct j71b_fw_record){p,len};
  if(i==0){for(j=0;j<11;j++)if(fw_le32(p+4*j)!=config[j])return -EINVAL;}
  else if(i==1){if(p[0]!=0x1a||p[1]!=0xa1||p[2]!=0x18||p[3]!=0xe1)return -EINVAL;}
  else if(i<=6){
   if(p[0]!=0x18||p[1]!=0xe1||p[2]!=0x30||p[3]!=1 ||
      (fw_be16(p+4)+1)*4!=len-16 || fw_xe32(p+6)!=addresses[i-2])return -EINVAL;
   for(j=4;j<10;j++)sum+=p[j];
   if(sum!=fw_be16(p+10))return -EINVAL;
   sum=0;for(j=12;j<len-4;j++)sum+=p[j];
   if(sum!=fw_xe32(p+len-4))return -EINVAL;
  }else if(i<=10){
   if(p[0]!=0x1e||p[1]!=0x33)return -EINVAL;
   for(j=2;j<14;j++)sum+=p[j];
   if(sum!=fw_be16(p+14))return -EINVAL;
  }else if(fw_le32(p)!=5000)return -EINVAL;
  end=pos+len;next=(end+3)&~(size_t)3;
  if(next>size)return -EINVAL;
  for(;end<next;end++)if(data[end])return -EINVAL;
  pos=next;
 }
 return pos==size?0:-EINVAL;
}
