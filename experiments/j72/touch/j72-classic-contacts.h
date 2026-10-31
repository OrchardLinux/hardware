/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef J72_CLASSIC_CONTACTS_H
#define J72_CLASSIC_CONTACTS_H
/* Portable bounded decoder. Caller verifies complete wire checksums first. */
#define J72_CONTACT_MAX 10
struct j72_contact { unsigned int id, state; int x, y; };
static int j72_signed16(const unsigned char *p)
{
 unsigned int v=p[0]|((unsigned int)p[1]<<8);
 return v&0x8000 ? (int)v-65536 : (int)v;
}
static int j72_classic_contacts(const unsigned char *p, unsigned int len,
 struct j72_contact *out, unsigned int *count)
{
 unsigned int header,n,stride,offset,i,j;
 if(len<24 || p[0]!=0x44)return -1;
 header=p[2];n=p[16];stride=p[17];
 offset=header+(p[14]|((unsigned int)p[15]<<8));
 /* Input qualification accepts the observed native-only J72 layout. */
 if(header!=24 || p[14] || p[15] || stride!=30 ||
    offset>len)return -2;
 if(n>J72_CONTACT_MAX)return -3;
 if(n>(len-offset)/stride)return -4;
 for(i=0;i<n;i++){
  const unsigned char *c=p+offset+i*stride;
  if(c[1]!=0 && c[1]!=3 && c[1]!=4 && c[1]!=5 && c[1]!=7)return -5;
  for(j=0;j<i;j++)if(out[j].id==c[0])return -6;
  out[i].id=c[0];out[i].state=c[1];
  out[i].x=j72_signed16(c+4);out[i].y=j72_signed16(c+6);
 }
 *count=n;return 0;
}
#endif
