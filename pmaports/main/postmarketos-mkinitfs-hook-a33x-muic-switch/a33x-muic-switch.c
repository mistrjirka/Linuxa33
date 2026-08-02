// SPDX-License-Identifier: MIT
#define _GNU_SOURCE

#include <errno.h>
#include <fcntl.h>
#include <linux/i2c-dev.h>
#include <linux/i2c.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <unistd.h>

#define I2C_DEVICE "/dev/i2c-2"
#define MUIC_ADDRESS 0x3e
#define REG_MUIC_CTRL1 0x6d
#define REG_MANUAL_SW_CTRL 0x70

#define CTRL1_MANUAL_OPEN 0x13
#define MANUAL_SWITCH_USB 0x24
#define CTRL1_AUTO_USB 0x17

static int smbus_access(int fd, char read_write, uint8_t command, int size,
                        union i2c_smbus_data *data)
{
    struct i2c_smbus_ioctl_data args = {
        .read_write = read_write,
        .command = command,
        .size = size,
        .data = data,
    };

    return ioctl(fd, I2C_SMBUS, &args);
}

static int read_byte_data(int fd, uint8_t reg, uint8_t *value)
{
    union i2c_smbus_data data;

    memset(&data, 0, sizeof(data));
    if (smbus_access(fd, I2C_SMBUS_READ, reg, I2C_SMBUS_BYTE_DATA, &data) < 0)
        return -1;

    *value = data.byte & 0xff;
    return 0;
}

static int write_byte_data(int fd, uint8_t reg, uint8_t value)
{
    union i2c_smbus_data data;

    memset(&data, 0, sizeof(data));
    data.byte = value;
    return smbus_access(fd, I2C_SMBUS_WRITE, reg, I2C_SMBUS_BYTE_DATA, &data);
}

static void print_errno(const char *operation, uint8_t reg)
{
    fprintf(stderr,
            "a33x-muic-switch-v1: ERROR operation=%s reg=0x%02x errno=%d detail=%s\n",
            operation, reg, errno, strerror(errno));
}

static int write_and_verify(int fd, uint8_t reg, uint8_t expected)
{
    uint8_t actual = 0;

    if (write_byte_data(fd, reg, expected) < 0) {
        print_errno("write", reg);
        return -1;
    }

    if (read_byte_data(fd, reg, &actual) < 0) {
        print_errno("readback", reg);
        return -1;
    }

    printf("a33x-muic-switch-v1: verify reg=0x%02x expected=0x%02x actual=0x%02x\n",
           reg, expected, actual);

    if (actual != expected) {
        fprintf(stderr,
                "a33x-muic-switch-v1: ERROR verify-mismatch reg=0x%02x expected=0x%02x actual=0x%02x\n",
                reg, expected, actual);
        errno = EIO;
        return -1;
    }

    return 0;
}

static void rollback(int fd, uint8_t original_ctrl1, uint8_t original_switch)
{
    int saved_errno = errno;
    bool ok = true;
    uint8_t actual = 0;

    fprintf(stderr,
            "a33x-muic-switch-v1: rollback-begin ctrl1=0x%02x switch=0x%02x\n",
            original_ctrl1, original_switch);

    if (write_byte_data(fd, REG_MUIC_CTRL1, CTRL1_MANUAL_OPEN) < 0)
        ok = false;
    if (write_byte_data(fd, REG_MANUAL_SW_CTRL, original_switch) < 0)
        ok = false;
    if (write_byte_data(fd, REG_MUIC_CTRL1, original_ctrl1) < 0)
        ok = false;

    if (read_byte_data(fd, REG_MUIC_CTRL1, &actual) < 0 || actual != original_ctrl1)
        ok = false;
    if (read_byte_data(fd, REG_MANUAL_SW_CTRL, &actual) < 0 || actual != original_switch)
        ok = false;

    fprintf(stderr, "a33x-muic-switch-v1: rollback-%s\n", ok ? "ok" : "failed");
    errno = saved_errno;
}

int main(void)
{
    int fd = -1;
    int rc = EXIT_FAILURE;
    uint8_t original_ctrl1 = 0;
    uint8_t original_switch = 0;
    uint8_t final_ctrl1 = 0;
    uint8_t final_switch = 0;
    bool modified = false;

    fd = open(I2C_DEVICE, O_RDWR | O_CLOEXEC);
    if (fd < 0) {
        fprintf(stderr,
                "a33x-muic-switch-v1: ERROR open path=%s errno=%d detail=%s\n",
                I2C_DEVICE, errno, strerror(errno));
        goto out;
    }

    if (ioctl(fd, I2C_SLAVE, MUIC_ADDRESS) < 0) {
        fprintf(stderr,
                "a33x-muic-switch-v1: ERROR select-slave bus=2 address=0x%02x errno=%d detail=%s\n",
                MUIC_ADDRESS, errno, strerror(errno));
        goto out;
    }

    if (read_byte_data(fd, REG_MUIC_CTRL1, &original_ctrl1) < 0) {
        print_errno("initial-read", REG_MUIC_CTRL1);
        goto out;
    }
    if (read_byte_data(fd, REG_MANUAL_SW_CTRL, &original_switch) < 0) {
        print_errno("initial-read", REG_MANUAL_SW_CTRL);
        goto out;
    }

    printf("a33x-muic-switch-v1: initial bus=2 address=0x%02x ctrl1=0x%02x switch=0x%02x\n",
           MUIC_ADDRESS, original_ctrl1, original_switch);

    modified = true;
    if (write_and_verify(fd, REG_MUIC_CTRL1, CTRL1_MANUAL_OPEN) < 0)
        goto fail_after_write;
    if (write_and_verify(fd, REG_MANUAL_SW_CTRL, MANUAL_SWITCH_USB) < 0)
        goto fail_after_write;
    if (write_and_verify(fd, REG_MUIC_CTRL1, CTRL1_AUTO_USB) < 0)
        goto fail_after_write;

    if (read_byte_data(fd, REG_MUIC_CTRL1, &final_ctrl1) < 0) {
        print_errno("final-read", REG_MUIC_CTRL1);
        goto fail_after_write;
    }
    if (read_byte_data(fd, REG_MANUAL_SW_CTRL, &final_switch) < 0) {
        print_errno("final-read", REG_MANUAL_SW_CTRL);
        goto fail_after_write;
    }

    if (final_ctrl1 != CTRL1_AUTO_USB || final_switch != MANUAL_SWITCH_USB) {
        fprintf(stderr,
                "a33x-muic-switch-v1: ERROR final-mismatch ctrl1=0x%02x switch=0x%02x\n",
                final_ctrl1, final_switch);
        errno = EIO;
        goto fail_after_write;
    }

    printf("a33x-muic-switch-v1: success ctrl1=0x%02x switch=0x%02x\n",
           final_ctrl1, final_switch);
    rc = EXIT_SUCCESS;
    goto out;

fail_after_write:
    if (modified)
        rollback(fd, original_ctrl1, original_switch);

out:
    if (fd >= 0 && close(fd) < 0 && rc == EXIT_SUCCESS) {
        fprintf(stderr,
                "a33x-muic-switch-v1: ERROR close errno=%d detail=%s\n",
                errno, strerror(errno));
        rc = EXIT_FAILURE;
    }
    return rc;
}
