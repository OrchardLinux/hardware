"""Execute the actual charge policy/detector with scripted I2C, ADC and PM.

No device or calibration file is used. The 24-byte arithmetic fixture is
synthetic. Compile extracted functions so the test exercises production C.
"""
from pathlib import Path
import os
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
POWER = ROOT / 'kernel/modules/j71b/power'


def function(text, name):
    start = re.search(r'^static [^\n]+\b' + name + r'\([^;{]*\)\s*{',
                      text, re.M).start()
    brace = text.index('{', start)
    depth = 1
    end = brace + 1
    while depth:
        depth += (text[end] == '{') - (text[end] == '}')
        end += 1
    return text[start:end] + '\n'


class Charging(unittest.TestCase):
    def test_actual_policy_and_detector(self):
        cc = os.environ.get('CC') or shutil.which('clang') or shutil.which('cc')
        self.assertIsNotNone(cc, 'C compiler required for charging safety tests')
        source = (POWER / 'j71b_charge_policy.c').read_text()
        globals_ = source[source.index('#define STATUS_REG'):source.index('module_param(source_aware')]
        names = ['write_checked', 'verify_handoff_locked', 'restore_locked',
                 'lease_expired', 'check_battery', 'update_source_locked',
                 'baseline', 'owned_fields_locked', 'policy_tick_locked',
                 'stop_locked', 'source_poll', 'repair_route', 'restore_route',
                 'set_lease', 'policy_pm']
        code = STUBS + globals_ + '\n#include "j71b-charge-math.h"\n#include "j71b-source.h"\n'
        # Delayed works need declarations before the extracted functions.
        code += 'static struct delayed_work lease_work, source_work;\n'
        code += '\n'.join(function(source, name) for name in names)
        code += CASES
        with tempfile.TemporaryDirectory(prefix='j71b-charge-') as directory:
            c = Path(directory) / 'charge.c'
            binary = Path(directory) / 'charge'
            c.write_text(code)
            subprocess.run([cc, '-std=gnu11', '-O1', '-g', '-pthread',
                            '-fsanitize=address,undefined', '-fno-omit-frame-pointer',
                            '-I', str(POWER), str(c), '-o', str(binary)], check=True)
            subprocess.run([str(binary)], check=True, timeout=30)


STUBS = r'''
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <pthread.h>
#include <unistd.h>
#ifndef EUCLEAN
#define EUCLEAN 117
#endif
static int kstrtobool(const char *s, bool *on) {
 if (!strcmp(s,"1")) *on=true;
 else if (!strcmp(s,"0")) *on=false;
 else return -EINVAL;
 return 0;
}
typedef uint8_t u8;
typedef int32_t s32;
typedef int64_t s64;
#define BIT(n) (1U << (n))
#define GENMASK(h,l) (((1U << ((h)+1))-1) & ~((1U << (l))-1))
#define ARRAY_SIZE(a) (sizeof(a)/sizeof((a)[0]))
#define min(a,b) ((a)<(b)?(a):(b))
#define max(a,b) ((a)>(b)?(a):(b))
#define min_t(t,a,b) min((t)(a),(t)(b))
#define HZ 100
#define DEFINE_MUTEX(n) pthread_mutex_t n = PTHREAD_MUTEX_INITIALIZER
static int test_mutex_lock(pthread_mutex_t *lock);
#define mutex_lock test_mutex_lock
#define mutex_unlock pthread_mutex_unlock
#define READ_ONCE(x) (x)
#define time_before(a,b) ((long)((a)-(b)) < 0)
#define time_after_eq(a,b) (!time_before(a,b))
#define pr_info(...) ((void)0)
#define pr_warn_ratelimited(...) ((void)0)
#define pr_crit(...) ((void)0)
#define pr_err(...) ((void)0)
#define module_param_named(...)
#define module_param(...)
#define MODULE_PARM_DESC(...)
#define I2C_LOCK_SEGMENT 0
#define I2C_SMBUS_WRITE 0
#define I2C_SMBUS_READ 1
#define I2C_SMBUS_BYTE 0
#define PM_SUSPEND_PREPARE 1
#define PM_HIBERNATION_PREPARE 2
#define PM_RESTORE_PREPARE 3
#define PM_POST_SUSPEND 4
#define PM_POST_HIBERNATION 5
#define PM_POST_RESTORE 6
#define NOTIFY_OK 0
#define NOTIFY_DONE 0
static int notifier_from_errno(int error) { return error; }
struct work_struct { int unused; };
struct delayed_work { struct work_struct work; unsigned long delay; };
#define to_delayed_work(w) ((struct delayed_work *)(w))
struct kernel_param { int unused; };
struct notifier_block { int unused; };
struct device { int unused; };
struct device_node { int unused; };
struct regmap { int unused; };
struct i2c_client { void *adapter; unsigned int addr, flags; };
union i2c_smbus_data { u8 byte; };
struct j71b_usb_source { unsigned int generation; bool connected, active; };
struct power_supply { int unused; };
union power_supply_propval { int intval; };
enum { POWER_SUPPLY_PROP_PRESENT, POWER_SUPPLY_PROP_CAPACITY,
       POWER_SUPPLY_PROP_TEMP, POWER_SUPPLY_PROP_VOLTAGE_NOW,
       POWER_SUPPLY_PROP_CURRENT_NOW };
static unsigned long jiffies;
static int system_unbound_wq;
static unsigned int regs[0x5000], tristar[256];
static int battery_values[5], battery_fail;
static int writes, reads, fail_write, fail_read, corrupt_read, adc_count;
static int route_fail, pulse_fail, event_at_adc, adc_timeout, role_depth;
static int write_regs[128], write_values[128], adc_mv[8], adc_next;
static int selected, usb_error, role_error;
static int fail_restore;
static struct j71b_usb_source usb;
static pthread_mutex_t role_mutex = PTHREAD_MUTEX_INITIALIZER;
static pthread_mutex_t adc_mutex = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t adc_cond = PTHREAD_COND_INITIALIZER;
static bool block_adc, adc_entered, release_adc, pm_entered;
static bool block_cancel, cancel_entered, release_cancel, pm_waiting_renewal;
static _Thread_local bool pm_runner;
static s64 div_s64(s64 a, int b) { return a/b; }
static uint64_t div_u64(uint64_t a, unsigned int b) { return a/b; }
static unsigned int get_unaligned_le32(const u8 *p) {
 return p[0] | (unsigned)p[1]<<8 | (unsigned)p[2]<<16 | (unsigned)p[3]<<24;
}
static void msleep(unsigned int ms) { jiffies += ms * HZ / 1000; }
static void usleep_range(unsigned int lo, unsigned int hi) { (void)lo; (void)hi; }
static int regmap_write(struct regmap *map, unsigned int reg, unsigned int value) {
 assert(reg == 0xa03 || reg == 0xa08 || reg == 0x4000);
 assert(writes < 128); write_regs[writes]=reg; write_values[writes]=value;
 writes++;
 if (reg == 0x4000) { assert(role_depth == 1); adc_count++;
   if (adc_count == event_at_adc) usb.generation++;
   pthread_mutex_lock(&adc_mutex);
   if (block_adc) {
    adc_entered=true; pthread_cond_broadcast(&adc_cond);
    while (!release_adc) pthread_cond_wait(&adc_cond,&adc_mutex);
   }
   pthread_mutex_unlock(&adc_mutex);
 }
 if (fail_restore && reg == 0xa03 && value == 2) return -EIO;
 if (writes == fail_write) return -EIO;
 regs[reg]=value;
 if (reg == 0x4000 && !adc_timeout) regs[0x156]=1;
 return 0;
}
static int regmap_read(struct regmap *map, unsigned int reg, unsigned int *v) {
 reads++; if (reads == fail_read) return -EIO;
 *v=regs[reg]; if (reads == corrupt_read) *v ^= 1;
 return 0;
}
static int regmap_bulk_read(struct regmap *map, unsigned int reg, u8 *v, size_t n) {
 assert(reg == 0x4001 && n == 2 && adc_next < 8);
 unsigned int raw = (adc_mv[adc_next++]-40)*65536U/80000U;
 v[0]=raw & 15; v[1]=raw>>4; regs[0x156]=0; return 0;
}
#define read_poll_timeout(op,val,cond,sl,to,del,...) ({ \
 int rc=-ETIMEDOUT; for (int i=0;i<4;i++) { val=op(__VA_ARGS__); \
 if (cond) {rc=0;break;} } rc; })
static void i2c_lock_bus(void *a,int b) { (void)a; (void)b; }
static void i2c_unlock_bus(void *a,int b) { (void)a; (void)b; }
static int __i2c_smbus_xfer(void *a,unsigned int addr,unsigned int flags,int dir,
                           u8 reg,int size,union i2c_smbus_data *data) {
 if (dir == I2C_SMBUS_WRITE) selected=reg; else data->byte=tristar[selected];
 return 0;
}
static int i2c_smbus_write_byte_data(struct i2c_client *c,u8 reg,u8 value) {
 assert(role_depth == 1 && (reg == 1 || reg == 3));
 if ((reg == 1 && value == 1 && route_fail) ||
     (reg == 3 && value == 0 && pulse_fail)) return -EIO;
 tristar[reg]=value; return 0;
}
static struct power_supply *power_supply_get_by_name(const char *name) {
 static struct power_supply battery; (void)name; return &battery;
}
static void power_supply_put(struct power_supply *psy) { (void)psy; }
static int power_supply_get_property(struct power_supply *psy,int prop,
                                     union power_supply_propval *v) {
 if (prop == battery_fail) return -EIO; v->intval=battery_values[prop]; return 0;
}
static void mod_delayed_work(int q,struct delayed_work *w,unsigned long delay) {
 (void)q; w->delay=delay;
}
static void cancel_delayed_work_sync(struct delayed_work *w) {
 (void)w; pthread_mutex_lock(&adc_mutex);
 if (block_cancel) {
  block_cancel=false; cancel_entered=true; pthread_cond_broadcast(&adc_cond);
  while (!release_cancel) pthread_cond_wait(&adc_cond,&adc_mutex);
 }
 pthread_mutex_unlock(&adc_mutex);
}
static int usb_gadget_j71b_source(struct device_node *node,
                                 struct j71b_usb_source *s,bool detached) {
 if (usb_error) return usb_error;
 if (detached) { usb.connected=false; usb.generation++; }
 *s=usb; return 0;
}
static int dwc2_j71b_source_monitor(struct device_node *node,
                                   int (*sample)(void *),void *data) {
 if (role_error) return role_error;
 pthread_mutex_lock(&role_mutex); role_depth++;
 int ret=sample(data);
 role_depth--; pthread_mutex_unlock(&role_mutex); return ret;
}
'''

CASES = r'''
static int test_mutex_lock(pthread_mutex_t *lock) {
 if (pm_runner && lock==&renewal_lock) {
  pthread_mutex_lock(&adc_mutex); pm_waiting_renewal=true;
  pthread_cond_broadcast(&adc_cond); pthread_mutex_unlock(&adc_mutex);
 }
 return pthread_mutex_lock(lock);
}
static void reset(bool opt_in) {
 static struct i2c_client client;
 memset(regs,0,sizeof(regs)); memset(tristar,0,sizeof(tristar));
 memset(&source,0,sizeof(source)); source.type="unknown"; source.epoch=1;
 source.client=&client; source.present=true;
 source_aware=opt_in; calibrated=opt_in; source_ready=opt_in;
 role_safe=opt_in; ready=true; changed=parked=restore_failed=false;
 lease_active=true; charge_saved=false; expected_input=2;
 old_charge=expected_charge=20; old_suspend=0;
 regs[0xa03]=2; regs[0xa08]=20; regs[0xa0c]=0xf4; regs[0x18a]=0x43;
 tristar[0xf]=0x8a; tristar[0xc]=3; tristar[1]=1;
 battery_values[0]=1; battery_values[1]=50; battery_values[2]=250;
 battery_values[3]=3900000; battery_values[4]=500000;
 battery_fail=-1; nominal_ua=adaptive_ua=backoffs=0; adaptive_input=255;
 jiffies=1000; lease_deadline=jiffies+15*HZ; step_after=adaptive_after=0;
 platform_ua=2400000; policy_error=source_error=0;
 input_codes[0]=0x20; input_codes[1]=0x46;
 input_codes[2]=0xa2; input_codes[3]=0xba;
 writes=reads=fail_write=fail_read=corrupt_read=adc_count=0;
 fail_restore=0; block_adc=adc_entered=release_adc=pm_entered=false;
 block_cancel=cancel_entered=release_cancel=pm_waiting_renewal=false;
 route_fail=pulse_fail=event_at_adc=adc_timeout=adc_next=0;
 usb=(struct j71b_usb_source){.generation=1}; usb_error=role_error=0;
 adc_mv[0]=adc_mv[1]=2400; adc_mv[2]=600; adc_mv[3]=200;
}
static int tick(void) { int r=policy_tick_locked(); if(r) stop_locked(r); return r; }
static void full_host(void) {
 usb.connected=true;
 for(int i=0;i<4;i++) { assert(tick()==0); jiffies+=HZ; }
 assert(nominal_ua==2400000 && regs[0xa03]==0xba && regs[0xa08]==50);
}
static void test_defaults(void) {
 reset(false); assert(tick()==0); assert(regs[0xa03]==0x1c && writes==1);
 assert(!charge_saved && adc_count==0);
 assert(set_lease("0",NULL)==0 && regs[0xa03]==2);
 reset(true); calibrated=source_ready=false; assert(tick()==0);
 assert(regs[0xa03]==0x1c && regs[0xa08]==20 && !adc_count);
 reset(false); regs[0xa03]=0x1c; assert(tick()==-EUCLEAN);
 assert(!writes && restore_failed); /* Do not adopt another owner's setting. */
}
static void test_guards_and_failures(void) {
 for(int prop=0;prop<5;prop++) {
  reset(true); full_host(); battery_fail=prop; assert(tick()==-EIO);
  assert(regs[0xa03]==2 && regs[0xa08]==20 && !lease_active);
 }
 int bad[][2]={{0,0},{1,14},{1,96},{2,49},{2,401},{3,3199999},
               {3,4350001},{4,-3000001},{4,3000001}};
 for(unsigned i=0;i<ARRAY_SIZE(bad);i++) {
  reset(true); battery_values[bad[i][0]]=bad[i][1];
  assert(tick()!=0 && writes==0);
 }
 for(int fail=1;fail<=2;fail++) {
  reset(true); usb.connected=true; fail_write=fail;
  assert(tick()!=0); assert(!lease_active && regs[0xa03]==2 && regs[0xa08]==20);
 }
 for(int fail=1;fail<=7;fail++) {
  reset(true); usb.connected=true; fail_read=fail;
  assert(tick()==-EIO && !lease_active);
  assert(regs[0xa03]==2 && regs[0xa08]==20);
 }
 for(int corrupt=6;corrupt<=7;corrupt++) {
  reset(true); usb.connected=true; corrupt_read=corrupt;
  assert(tick()==-EUCLEAN && restore_failed);
  assert(regs[0xa03]==2 && regs[0xa08]==20);
 }
 reset(true); full_host(); regs[0xa08]++; assert(tick()==-EUCLEAN);
 assert(restore_failed && regs[0xa03]==2 && regs[0xa08]==20);
 assert(set_lease("1",NULL)==-EBUSY); assert(set_lease("0",NULL)==0);
 assert(!restore_failed);
 reset(true); full_host(); regs[0xa03]++; assert(tick()==-EUCLEAN);
 reset(true); regs[0xa08]++; assert(tick()==-EUCLEAN && writes==0);
 reset(true); full_host(); fail_write=writes+1;
 assert(policy_pm(NULL,PM_SUSPEND_PREPARE,NULL)==0); /* bounded retry */
 assert(regs[0xa03]==2 && regs[0xa08]==20 && parked);
 reset(true); full_host(); fail_restore=1;
 assert(policy_pm(NULL,PM_SUSPEND_PREPARE,NULL)==-EIO);
 assert(restore_failed && !parked && !lease_active);
 assert(set_lease("1",NULL)==-EBUSY && set_lease("0",NULL)==-EIO);
 fail_restore=0; assert(set_lease("0",NULL)==0 && !restore_failed);
}
static void test_sources_and_epochs(void) {
 int mv[]={1900,2400,3100}; int expected[]={500,1000,1000,2100,2400,3000,1000,1000,1000};
 for(int p=0;p<3;p++) for(int m=0;m<3;m++)
  assert(divider_current(mv[p],mv[m])==expected[p*3+m]*1000);
 assert(divider_current(1499,2400)==0 && divider_current(-1,2400)==-ERANGE);
 for(int ceiling=500000;ceiling<=2400000;ceiling+=100000) {
  reset(true); platform_ua=ceiling; usb.connected=true;
  for(int i=0;i<4;i++) {assert(tick()==0);jiffies+=HZ;}
  assert(nominal_ua<=platform_ua);
 }
 reset(true); full_host(); usb.connected=false; usb.generation++;
 assert(tick()==0 && nominal_ua==2400000 && source.epoch==1);
 tristar[0xc]=0; assert(update_source_locked()==-ENOLINK);
 assert(source.epoch==2 && !lease_active && !source.computer && !source.sampled);
 assert(regs[0xa03]==2 && regs[0xa08]==20);
 tristar[0xc]=3; assert(update_source_locked()==0 && !lease_active);
 assert(set_lease("1",NULL)==0); assert(nominal_ua==425000);
 jiffies+=HZ;
 assert(dwc2_j71b_source_monitor(NULL,source_monitor,NULL)==0);
 assert(source.sampled && source.allowance==2400000 && !source.computer);
 reset(true); source.allowance=1500000; source.type="bc12-cdp";
 usb.connected=true;
 for(int i=0;i<4;i++){assert(tick()==0);jiffies+=HZ;}
 assert(!source.computer && nominal_ua==1000000);
 reset(true); full_host(); usb_error=-ENODEV; tristar[0xc]=0;
 assert(update_source_locked()==-ENODEV && source.forget_pending && !lease_active);
 tristar[0xc]=3; usb_error=0;
 assert(update_source_locked()==0 && !source.computer && !usb.connected);
 assert(!source.forget_pending && !source.allowance);
}
static void test_adc_and_route(void) {
 reset(true); usb.connected=true;
 assert(dwc2_j71b_source_monitor(NULL,source_monitor,NULL)==0 && !adc_count);
 for(int event=1;event<=4;event++) {
  reset(true); adc_mv[0]=adc_mv[1]=1000; event_at_adc=event;
  assert(dwc2_j71b_source_monitor(NULL,source_monitor,NULL)==-EAGAIN);
  assert(!source.sampled && !source.allowance && tristar[1]==1 && tristar[3]==0);
 }
 reset(true); adc_timeout=1;
 assert(dwc2_j71b_source_monitor(NULL,source_monitor,NULL)==-ETIMEDOUT);
 assert(source.adc_poisoned && !source.sampled && tristar[1]==1);
 tristar[0xc]=0; update_source_locked(); tristar[0xc]=3; update_source_locked();
 assert(source.adc_poisoned); /* Detach does not make an uncertain ADC fresh. */
 reset(true); route_fail=1;
 assert(dwc2_j71b_source_monitor(NULL,source_monitor,NULL)==-EUCLEAN);
 assert(source.route_pending && !source.sampled);
 assert(policy_pm(NULL,PM_SUSPEND_PREPARE,NULL)!=0);
 assert(restore_failed && !parked);
 route_fail=0; assert(set_lease("0",NULL)==0 && !restore_failed);
 assert(tristar[1]==1 && tristar[3]==0);
 reset(true); adc_mv[0]=adc_mv[1]=1000;
 assert(dwc2_j71b_source_monitor(NULL,source_monitor,NULL)==0);
 assert(!strcmp(source.type,"bc12-dcp") && source.allowance==1500000);
 reset(true); adc_mv[0]=adc_mv[1]=1000; adc_mv[3]=400;
 assert(dwc2_j71b_source_monitor(NULL,source_monitor,NULL)==0);
 assert(!strcmp(source.type,"bc12-cdp"));
 reset(true); calibrated=false;
 assert(dwc2_j71b_source_monitor(NULL,source_monitor,NULL)==0 && !adc_count);
 reset(true); lease_active=false;
 assert(dwc2_j71b_source_monitor(NULL,source_monitor,NULL)==0 && !adc_count);
}
static void test_backoff_and_pm(void) {
 reset(true); full_host(); role_error=-EAGAIN;
 source_poll(&source_work.work);
 assert(lease_active && nominal_ua==2400000 && source.epoch==1);
 role_error=-EOPNOTSUPP; source_poll(&source_work.work);
 assert(!lease_active && regs[0xa03]==2 && regs[0xa08]==20);
 reset(true); full_host(); regs[0xa03]=0x90;
 assert(tick()==0 && backoffs==1 && nominal_ua==1000000 && regs[0xa03]<=0x90);
 jiffies+=59*HZ; lease_deadline=jiffies+15*HZ; assert(tick()==0);
 assert(nominal_ua==1000000);
 jiffies+=HZ; lease_deadline=jiffies+15*HZ; assert(tick()==0);
 assert(nominal_ua==2100000); /* At most one upward rung per minute. */
 regs[0xa03]=1; assert(tick()==0 && regs[0xa03]==1);
 assert(tick()==0 && regs[0xa03]==1); /* No immediate raise below floor. */
 assert(policy_pm(NULL,PM_SUSPEND_PREPARE,NULL)==0);
 assert(parked && !lease_active && regs[0xa03]==2 && regs[0xa08]==20);
 assert(set_lease("1",NULL)==-EBUSY); int n=writes;
 assert(policy_pm(NULL,PM_POST_SUSPEND,NULL)==0 && writes==n && !lease_active);
 assert(!source.computer && source.pm_blocked);
 assert(set_lease("1",NULL)==0 && nominal_ua==425000);
 int a=adc_count; assert(dwc2_j71b_source_monitor(NULL,source_monitor,NULL)==0);
 assert(adc_count==a); /* No ADC against a stale configured link after sleep. */
 reset(true); full_host(); jiffies=lease_deadline;
 lease_expired(&lease_work.work); assert(regs[0xa03]==2 && regs[0xa08]==20);
 assert(!lease_active);
}
static void test_calibration(void) {
 u8 data[24]={0},code; int points[]={100,475,1005,2100,2400,0};
 for(int i=0;i<5;i++) { unsigned v=points[i]*65536U; memcpy(data+i*4,&v,4); }
 data[20]=1;
 for(unsigned i=0;i<ARRAY_SIZE(source_steps);i++) {
  assert(calibrate_input(data,sizeof(data),source_steps[i]/1000,&code)==0);
  assert(code>2 && 75000+code*12500U<=(unsigned)source_steps[i]);
 }
 assert(calibrate_input(data,23,500,&code)==-EINVAL);
 data[20]=2; assert(calibrate_input(data,24,500,&code)==-EINVAL);
}
static void *monitor_thread(void *unused) {
 assert(dwc2_j71b_source_monitor(NULL,source_monitor,NULL)==0); return NULL;
}
static void *pm_thread(void *unused) {
 pm_runner=true;
 pthread_mutex_lock(&adc_mutex); pm_entered=true;
 pthread_cond_broadcast(&adc_cond); pthread_mutex_unlock(&adc_mutex);
 assert(policy_pm(NULL,PM_SUSPEND_PREPARE,NULL)==0); return NULL;
}
static void test_pm_interleaving(void) {
 pthread_t sampling, suspending;
 reset(true); assert(tick()==0); block_adc=true;
 assert(!pthread_create(&sampling,NULL,monitor_thread,NULL));
 pthread_mutex_lock(&adc_mutex);
 while (!adc_entered) pthread_cond_wait(&adc_cond,&adc_mutex);
 assert(pthread_mutex_trylock(&policy_lock)==EBUSY);
 assert(!pthread_create(&suspending,NULL,pm_thread,NULL));
 while (!pm_entered) pthread_cond_wait(&adc_cond,&adc_mutex);
 release_adc=true; pthread_cond_broadcast(&adc_cond);
 pthread_mutex_unlock(&adc_mutex);
 pthread_join(sampling,NULL); pthread_join(suspending,NULL);
 assert(parked && !lease_active && regs[0xa03]==2 && regs[0xa08]==20);
 assert(!source.route_pending && !source.dcp_pending);
}
static void *zero_thread(void *unused) {
 assert(set_lease("0",NULL)==0); return NULL;
}
static void test_zero_pm_interleaving(void) {
 pthread_t clearing, suspending;
 reset(true); full_host(); block_cancel=true;
 assert(!pthread_create(&clearing,NULL,zero_thread,NULL));
 pthread_mutex_lock(&adc_mutex);
 while (!cancel_entered) pthread_cond_wait(&adc_cond,&adc_mutex);
 assert(!pthread_mutex_trylock(&policy_lock)); pthread_mutex_unlock(&policy_lock);
 assert(pthread_mutex_trylock(&renewal_lock)==EBUSY);
 assert(!pthread_create(&suspending,NULL,pm_thread,NULL));
 while (!pm_waiting_renewal) pthread_cond_wait(&adc_cond,&adc_mutex);
 assert(!parked); /* PM cannot finish while zero is outside policy_lock. */
 release_cancel=true; pthread_cond_broadcast(&adc_cond);
 pthread_mutex_unlock(&adc_mutex);
 pthread_join(clearing,NULL); pthread_join(suspending,NULL);
 assert(parked && !lease_active && regs[0xa03]==2 && regs[0xa08]==20);
}
int main(void) {
 test_defaults(); test_guards_and_failures(); test_sources_and_epochs();
 test_adc_and_route(); test_backoff_and_pm(); test_calibration(); test_pm_interleaving();
 test_zero_pm_interleaving();
 puts("PASS actual J71b policy: defaults, source matrix, ADC cancellation, route repair, guards, rollback, epochs, backoff, PM and synthetic calibration");
 return 0;
}
'''
