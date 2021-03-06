
# pyAesCrypt module

# pyAesCrypt主要通过python的加密模块cryptography完成加解密环节
# 思路为：
# 输入passwd
# 随机生成初始变量iv1,iv1和passwd通过SHA256算法得到256bits的加密密钥key
# 随机生成256bts加密密钥intkey，并用intkey和随机的iv0生成AES对象AES0，主要用于加密文件
# 用key和iv1生成AES对象AES1,主要用于加密intkey和iv0
# 用intkey生成HMAC算法对象HMAC0，用于对整个加密文件做数字签名
# key生成HMAC1主要对intkey和iv0做数字签名
# 将文件描述信息和iv1明文和（iv0+intkey）加密后的信息和其数字签名写入输出文件作为文件头
# 解密时读出文件头中的iv1 加密后的(iv0+intkey)和其数字签名
# 输入的password和读到的iv1生成key，从而通过AES对象解密iv0+intKey
# 同时用key生成hmac对象验证iv0+intKey是否被修改
# 解密iv0和intkey，从而生产AES对象AES0，从而解密整个文件
# 最后256位为整个加密文件的hmac算法得到的数字签名，也能用来验证整个签名文件是否被修改


from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes, hmac
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from os import urandom
from os import stat, remove

# pyAesCrypt version
version = "0.3"

# encryption/decryption buffer size - 64K
bufferSize = 64 * 1024

# maximum password length (number of chars)
maxPassLen = 1024

#定义每次加密块大小为16*8 = 128 bits
# AES block size in bytes
AESBlockSize = 16

#通过hash算法SHA256将输入的密码和初始变量生成256bits长度的密钥
# password stretching function
def stretch(passw, iv1):

    # hash the external iv and the password 8192 times
    digest = iv1 + (16 * b"\x00")

    for i in range(8192):
        passHash = hashes.Hash(hashes.SHA256(), backend=default_backend())
        #等同于 passHash.update(digest+bytes(passw, "utf_16_le"))
        passHash.update(digest)
        passHash.update(bytes(passw, "utf_16_le"))
        digest = passHash.finalize()

    return digest


# encrypting function
# arguments:
# infile: plaintext file path
# outfile: ciphertext file path
# passw: encryption password
# bufferSize: encryption buffer size, must be a multiple of
#             AES block size (16)
#             using a larger buffer speeds up things when dealing
#             with big files
def encryptFile(infile, outfile, passw, bufferSize):
    # validate bufferSize
    if bufferSize % AESBlockSize != 0:
        raise ValueError("Buffer size must be a multiple of AES block size.")

    if len(passw) > maxPassLen:
        raise ValueError("Password is too long.")

    try:
        with open(infile, "rb") as fIn:
            # generate external iv (used to encrypt the main iv and the
            # encryption key)
            #随机生成AWESBlockSize大小字节的字符串用作加密初始变量
            iv1 = urandom(AESBlockSize)

            # stretch password and iv
            #生成256bits的加密密钥
            key = stretch(passw, iv1)

            # generate random main iv
            iv0 = urandom(AESBlockSize)

            # generate random internal key
            intKey = urandom(32)

            # instantiate AES cipher
            #使用python的加密模块，进行AES加密
            cipher0 = Cipher(algorithms.AES(intKey), modes.CBC(iv0),
                             backend=default_backend())
            encryptor0 = cipher0.encryptor()

            # instantiate HMAC-SHA256 for the ciphertext
            #生成HMAC-SHA256算法的一个实例
            hmac0 = hmac.HMAC(intKey, hashes.SHA256(),
                              backend=default_backend())

            # instantiate another AES cipher
            cipher1 = Cipher(algorithms.AES(key), modes.CBC(iv1),
                             backend=default_backend())
            encryptor1 = cipher1.encryptor()

            # encrypt main iv and key
            c_iv_key = encryptor1.update(iv0 + intKey) + encryptor1.finalize()

            # calculate HMAC-SHA256 of the encrypted iv and key
            hmac1 = hmac.HMAC(key, hashes.SHA256(),
                              backend=default_backend())
            hmac1.update(c_iv_key)

            try:
                with open(outfile, "wb") as fOut:
                    # write header 写入必要数据和描述
                    fOut.write(bytes("AES", "utf8"))

                    # write version (AES Crypt version 2 file format -
                    # see https://www.aescrypt.com/aes_file_format.html)
                    fOut.write(b"\x02")

                    # reserved byte (set to zero)
                    fOut.write(b"\x00")

                    # setup "CREATED-BY" extension
                    cby = "pyAesCrypt " + version

                    # write "CREATED-BY" extension length
                    fOut.write(b"\x00" + bytes([1+len("CREATED_BY"+cby)]))

                    # write "CREATED-BY" extension
                    fOut.write(bytes("CREATED_BY", "utf8") + b"\x00" +
                               bytes(cby, "utf8"))

                    # write "container" extension length
                    fOut.write(b"\x00\x80")

                    # write "container" extension
                    for i in range(128):
                        fOut.write(b"\x00")

                    # write end-of-extensions tag
                    fOut.write(b"\x00\x00")

                    # write the iv used to encrypt the main iv and the
                    # encryption key
                    fOut.write(iv1)

                    # write encrypted main iv and key
                    # encryptor1是由iv1和输入的passwd生成的，所以只需要铭文写入iv1
                    # 解密时输入passwd就能得到Initkey和iv0
                    # c_iv_key = encryptor1.update(iv0 + intKey) + encryptor1.finalize()
                    fOut.write(c_iv_key)

                    # write HMAC-SHA256 of the encrypted iv and key
                    # 写入c_iv_key通过hmac算法生成的签名
                    fOut.write(hmac1.finalize())

                    # encrypt file while reading it
                    while True:
                        # try to read bufferSize bytes
                        fdata = fIn.read(bufferSize)

                        # get the real number of bytes read
                        bytesRead = len(fdata)

                        # check if EOF was reached
                        if bytesRead < bufferSize:
                            # file size mod 16, lsb positions
                            fs16 = bytes([bytesRead % AESBlockSize])
                            # pad data (this is NOT PKCS#7!)
                            # ...unless no bytes or a multiple of a block size
                            # of bytes was read
                            if bytesRead % AESBlockSize == 0:
                                padLen = 0
                            else:
                                padLen = 16 - bytesRead % AESBlockSize
                            # 不足AESAESBlockSize则进行填充，填充数据为padlen位不足长度数值
                            fdata += bytes([padLen])*padLen
                            # encrypt data
                            #encryptor0.finalize()返回加密后的数据
                            cText = encryptor0.update(fdata) \
                                    + encryptor0.finalize()
                            # update HMAC
                            # 将AES加密后的数据在通过hmac加密生成签名
                            hmac0.update(cText)
                            # write encrypted file content
                            fOut.write(cText)
                            # break
                            break
                        # ...otherwise a full bufferSize was read
                        else:
                            # encrypt data
                            cText = encryptor0.update(fdata)
                            # update HMAC
                            hmac0.update(cText)
                            # write encrypted file content
                            fOut.write(cText)

                    # write plaintext file size mod 16 lsb positions
                    fOut.write(fs16)

                    # write HMAC-SHA256 of the encrypted file
                    # 写入整个加密文件通过hmac0生成的签名
                    fOut.write(hmac0.finalize())

            except IOError:
                raise IOError("Unable to write output file.")

    except IOError:
        raise IOError("File \"" + infile + "\" was not found.")


# decrypting function
# arguments:
# infile: ciphertext file path
# outfile: plaintext file path
# passw: encryption password
# bufferSize: decryption buffer size, must be a multiple of AES block size (16)
#             using a larger buffer speeds up things when dealing with
#             big files
def decryptFile(infile, outfile, passw, bufferSize):
    # validate bufferSize
    if bufferSize % AESBlockSize != 0:
        raise ValueError("Buffer size must be a multiple of AES block size")

    if len(passw) > maxPassLen:
        raise ValueError("Password is too long.")

    # get input file size
    inputFileSize = stat(infile).st_size

    try:
        with open(infile, "rb") as fIn:
            fdata = fIn.read(3)
            # check if file is in AES Crypt format (also min length check)
            if (fdata != bytes("AES", "utf8") or inputFileSize < 136):
                    raise ValueError("File is corrupted or not an AES Crypt "
                                     "(or pyAesCrypt) file.")

            # check if file is in AES Crypt format, version 2
            # (the only one compatible with pyAesCrypt)
            fdata = fIn.read(1)
            if len(fdata) != 1:
                raise ValueError("File is corrupted.")

            if fdata != b"\x02":
                raise ValueError("pyAesCrypt is only compatible with version "
                                 "2 of the AES Crypt file format.")

            # skip reserved byte
            fIn.read(1)

            # skip all the extensions
            while True:
                fdata = fIn.read(2)
                if len(fdata) != 2:
                    raise ValueError("File is corrupted.")
                if fdata == b"\x00\x00":
                    break
                fIn.read(int.from_bytes(fdata, byteorder="big"))

            # read external iv
            iv1 = fIn.read(16)
            if len(iv1) != 16:
                raise ValueError("File is corrupted.")

            # stretch password and iv
            # 得到加密的key
            key = stretch(passw, iv1)

            # read encrypted main iv and key
            c_iv_key = fIn.read(48)
            if len(c_iv_key) != 48:
                raise ValueError("File is corrupted.")

            # read HMAC-SHA256 of the encrypted iv and key
            hmac1 = fIn.read(32)
            if len(hmac1) != 32:
                raise ValueError("File is corrupted.")

            # compute actual HMAC-SHA256 of the encrypted iv and key
            hmac1Act = hmac.HMAC(key, hashes.SHA256(),
                                 backend=default_backend())
            hmac1Act.update(c_iv_key)

            # HMAC check
            # 签名验证
            if hmac1 != hmac1Act.finalize():
                raise ValueError("Wrong password (or file is corrupted).")

            # instantiate AES cipher
            cipher1 = Cipher(algorithms.AES(key), modes.CBC(iv1),
                             backend=default_backend())
            decryptor1 = cipher1.decryptor()

            # decrypt main iv and key
            iv_key = decryptor1.update(c_iv_key) + decryptor1.finalize()

            # get internal iv and key
            # iv_key前16位为初始变量iv0，后16位是intkey
            iv0 = iv_key[:16]
            intKey = iv_key[16:]

            # instantiate another AES cipher
            cipher0 = Cipher(algorithms.AES(intKey), modes.CBC(iv0),
                             backend=default_backend())
            decryptor0 = cipher0.decryptor()

            # instantiate actual HMAC-SHA256 of the ciphertext
            hmac0Act = hmac.HMAC(intKey, hashes.SHA256(),
                                 backend=default_backend())

            try:
                with open(outfile, "wb") as fOut:
                    while fIn.tell() < inputFileSize - 32 - 1 - bufferSize:
                        # read data
                        cText = fIn.read(bufferSize)
                        # update HMAC
                        hmac0Act.update(cText)
                        # decrypt data and write it to output file
                        fOut.write(decryptor0.update(cText))

                    # decrypt remaining ciphertext, until last block is reached
                    while fIn.tell() < inputFileSize - 32 - 1 - AESBlockSize:
                        # read data
                        cText = fIn.read(AESBlockSize)
                        # update HMAC
                        hmac0Act.update(cText)
                        # decrypt data and write it to output file
                        fOut.write(decryptor0.update(cText))

                    # last block reached, remove padding if needed
                    # read last block

                    # this is for empty files
                    if fIn.tell() != inputFileSize - 32 - 1:
                        cText = fIn.read(AESBlockSize)
                        if len(cText) < AESBlockSize:
                            # remove outfile and raise exception
                            remove(outfile)
                            raise ValueError("File is corrupted.")
                    else:
                        cText = bytes()

                    # update HMAC
                    hmac0Act.update(cText)

                    # read plaintext file size mod 16 lsb positions
                    fs16 = fIn.read(1)
                    if len(fs16) != 1:
                        # remove outfile and raise exception
                        remove(outfile)
                        raise ValueError("File is corrupted.")

                    # decrypt last block
                    pText = decryptor0.update(cText) + decryptor0.finalize()

                    # remove padding
                    toremove = ((16 - fs16[0]) % 16)
                    if toremove != 0:
                        pText = pText[:-toremove]

                    # write decrypted data to output file
                    fOut.write(pText)

                    # read HMAC-SHA256 of the encrypted file
                    hmac0 = fIn.read(32)
                    if len(hmac0) != 32:
                        # remove outfile and raise exception
                        remove(outfile)
                        raise ValueError("File is corrupted.")

                    # HMAC check
                    if hmac0 != hmac0Act.finalize():
                        # remove outfile and raise exception
                        remove(outfile)
                        raise ValueError("Bad HMAC (file is corrupted).")

            except IOError:
                raise IOError("Unable to write output file.")

    except IOError:
        raise IOError("File \"" + infile + "\" was not found.")
