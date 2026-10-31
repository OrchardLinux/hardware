#include <assert.h>
#include <stdio.h>
#include <string.h>
#include "j72-classic-contacts.h"
int main(void){
 unsigned char p[4096]={0},size[2];struct j72_contact c[10];unsigned int n,len,i,frames=0,contacts=0;
 p[0]=0x44;p[2]=24;p[17]=30;
 assert(!j72_classic_contacts(p,24,c,&n)&&n==0);
 p[16]=1;p[24]=1;p[25]=4;p[28]=0x90;p[29]=0xff;
 assert(!j72_classic_contacts(p,54,c,&n)&&n==1&&c[0].x==-112);
 for(i=0;i<54;i++)assert(j72_classic_contacts(p,i,c,&n));
 p[16]=2;p[54]=1;p[55]=4;assert(j72_classic_contacts(p,84,c,&n));
 p[54]=2;assert(!j72_classic_contacts(p,84,c,&n)&&n==2);
 p[55]=255;assert(j72_classic_contacts(p,84,c,&n));p[55]=4;
 for(i=11;i<256;i++){p[16]=i;assert(j72_classic_contacts(p,sizeof(p),c,&n));}
 p[16]=0;p[14]=1;assert(j72_classic_contacts(p,sizeof(p),c,&n));p[14]=0;
 for(i=0;i<256;i++)if(i!=30){p[17]=i;assert(j72_classic_contacts(p,sizeof(p),c,&n));}
 while(fread(size,1,2,stdin)==2){len=size[0]|size[1]<<8;assert(len<=sizeof(p));assert(fread(p,1,len,stdin)==len);assert(!j72_classic_contacts(p,len,c,&n));frames++;contacts+=n;}
 assert(!ferror(stdin));printf("PASS bounded malformed cases; actual frames=%u contact records=%u\n",frames,contacts);return 0;
}
