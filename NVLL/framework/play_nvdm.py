"Data20news_Distnor_Modelnvdm_Emb400_Hid400_lat50_lr0.0001_drop0.2_kappa0.1"

import os
import random
import time

import numpy
import scipy
import torch

from  NVLL.data.ng import DataNg
from NVLL.framework.run_nvdm import Runner
from NVLL.model.nvdm import BowVAE
from NVLL.util.util import GVar


class PlayNVDM():
    def __init__(self, load_path, load_name, data_path):
        self.args = self.load_args(load_path, load_name)
        self.data = self.load_data(data_path)
        self.model = self.load_model(load_path, load_name)

    def load_data(self, data_path):
        self.args.data_path = data_path
        data = DataNg(self.args)
        return data

    def load_args(self, path, name):
        with open(os.path.join(path, name + '.args'), 'rb') as f:
            args = torch.load(f)
        return args

    def load_model(self, path, name):
        model = BowVAE(self.args, vocab_size=2000, n_hidden=self.args.nhid, n_lat=self.args.lat_dim,
                       n_sample=3, dist=self.args.dist)
        model.load_state_dict(torch.load(os.path.join(path, name + '.model')))
        model = model.cuda()
        return model

    def eva(self):
        # Load the best saved model.
        cur_loss, cur_kl, test_loss = self.evaluate(self.args, self.model,
                                                    self.data.test)
        Runner.log_eval(cur_loss, cur_kl, test_loss, True)

    def evaluate(self, args, model, dev_batches):
        """
        Standard evaluation function on dev or test set.
        :param args:
        :param model:
        :param dev_batches:
        :return:
        """

        # Turn on training mode which enables dropout.
        model.eval()
        model.FLAG_train = False

        acc_loss = 0
        acc_kl_loss = 0
        acc_total_loss = 0
        all_cnt = 0
        start_time = time.time()

        for idx, batch in enumerate(dev_batches):
            feed = self.data.get_feed(batch)
            target = GVar(batch)
            seq_len, batch_sz = batch.size()
            tup, kld, decoded = model(feed, target)

            flatten_decoded = decoded.view(-1, self.model.ntoken)
            flatten_target = target.view(-1)
            loss = self.criterion(flatten_decoded, flatten_target)  # batch_sz * seq, loss
            sum_kld = torch.sum(kld)
            total_loss = loss + sum_kld * self.args.kl_weight

            acc_total_loss += loss.data * seq_len * batch_sz + sum_kld.data
            acc_loss += loss.data * seq_len * batch_sz
            acc_kl_loss += sum_kld.data
            all_cnt += batch_sz * seq_len

        # word ppl
        cur_loss = acc_loss[0] / all_cnt  # word loss
        cur_kl = acc_kl_loss[0] / all_cnt
        # cur_real_loss = acc_real_loss / doc_cnt
        cur_real_loss = cur_loss + cur_kl
        elapsed = time.time() - start_time

        # Runner.log_eval(print_ppl)
        # print('loss {:5.2f} | KL {:5.2f} | ppl {:8.2f}'.format(            cur_loss, cur_kl, math.exp(print_ppl)))
        return cur_loss, cur_kl, cur_real_loss

    def play_eval(self, args, model, train_batches, epo, epo_start_time, glob_iter):
        # reveal the relation between latent space and length and loss
        # reveal the distribution of latent space
        model.eval()
        start_time = time.time()
        acc_loss = 0
        acc_kl_loss = 0
        acc_real_loss = 0

        word_cnt = 0
        doc_cnt = 0

        random.shuffle(train_batches)

        if self.args.dist == 'nor':
            vs = visual_gauss()
        elif self.args.dist == 'vmf':
            vs = visual_vmf()

        for idx, batch in enumerate(train_batches):
            # seq_len, batch_sz = batch.size()
            data_batch, count_batch = DataNg.fetch_data(
                self.data.test[0], self.data.test[1], batch)

            data_batch = GVar(torch.FloatTensor(data_batch))

            recon_loss, kld, total_loss, tup, vecs = model(data_batch)

            vs.add_batch(data_batch, tup, kld.data, vecs)

            count_batch = torch.FloatTensor(count_batch).cuda()
            real_loss = torch.div((recon_loss + kld).data, count_batch)
            doc_num = len(count_batch)
            # remove nan
            for n in real_loss:
                if n == n:
                    acc_real_loss += n
            # acc_real_ppl += torch.sum(real_ppl)

            acc_loss += torch.sum(recon_loss).data  #
            acc_kl_loss += torch.sum(kld.data)
            count_batch = count_batch + 1e-12

            word_cnt += torch.sum(count_batch)
            doc_cnt += doc_num

        cur_loss = acc_loss[0] / word_cnt  # word loss
        cur_kl = acc_kl_loss / word_cnt
        # cur_real_loss = acc_real_loss / doc_cnt
        cur_real_loss = cur_loss + cur_kl

        Runner.log_instant(None, self.args, glob_iter, epo, start_time, cur_loss
                           , cur_kl,
                           cur_real_loss)
        vs.write_log()


class visual_gauss():
    def __init__(self, d=None):
        self.logs = []
        self.dict = d

    def add_batch(self, data_batch, tup, kld, vecs):
        __batch = kld.size()[0]
        mean = tup['mean']
        logvar = tup['logvar']
        # print(target.size())
        # print(batch_sz)
        for b in range(__batch):
            this_mean = mean[b]
            this_logvar = logvar[b]
            self.add_single(this_mean, this_logvar)

    def add_single(self, mean, logvar):
        norm_mean = torch.norm(mean).data[0]
        norm_var = torch.norm(torch.exp(logvar)).data[0]

        self.logs.append("{}\t{}".format(norm_mean, norm_var))

    def write_log(self):
        with open('gauss_log.txt', 'w') as f:
            f.write('\n'.join(self.logs))


class visual_vmf():
    def __init__(self, d=None):
        self.logs = []
        self.dict = d

    def add_batch(self, target, tup, kld, loss):
        seq_len, batch_sz = loss.size()
        _seq_len, _batch_sz = target.size()
        # __batch = kld.size()[0]
        assert seq_len == _seq_len
        assert batch_sz == _batch_sz
        mu = tup['mu']
        # print(target.size())
        # print(batch_sz)
        for b in range(batch_sz):
            this_target = target[:, b]
            this_mu = mu[b]
            this_loss = loss[:, b]
            self.add_single(this_target, this_mu,
                            this_loss)

    def add_single(self, target, mu, loss):
        thismu = mu.data
        length = len(target)
        seq = ''
        for t in target:
            seq += self.dict.idx2word[t] + '_'

        # self.logs.append("{}\t{}\t{}\t{}\t{}\t{}".format(norm_mean,kld,torch.mean(loss)
        #                                      ,length, seq))
        tmp = []
        for i in thismu:
            tmp.append(str(i))
        s = '\t'.join(tmp)
        self.logs.append(s)

    def write_log(self):
        with open('vc.txt', 'w') as f:
            f.write('\n'.join(self.logs))


if __name__ == '__main__':
    player = PlayNVDM('/home/jcxu/vae_txt/NVLL',
                      'Data20news_Distnor_Modelnvdm_Emb400_Hid400_lat50_lr0.0001_drop0.2_kappa0.1'
                      , '/home/jcxu/vae_txt/data/20news')
    # player.eva()
    # glob_iter = self.train_epo(self.args, self.model, self.data.train_batches, epoch,
    #                            epoch_start_time, glob_iter)
    player.play_eval(player.args, player.model, player.data.test_batches, 0, 0, 0)

    os.chdir('/home/jcxu/vae_txt/NVLL/framework')
