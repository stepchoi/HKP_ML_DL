'''This code would import TABLE main and perform:

    1. add lag period
    2. add macro & y
    3. extract:
        a. by period desired
        b. by random sampling
    4. clean by:
        x -> standardization
        y -> qcut

'''
import datetime as dt
import gc
import time
from collections import Counter

import numpy as np
import pandas as pd
from dateutil.relativedelta import relativedelta
from sklearn.preprocessing import StandardScaler
from sklearn.utils import shuffle
from sqlalchemy import create_engine
from tqdm import tqdm


def check_print(df_list):
    df = pd.concat(df_list, axis=1)
    col = ['gvkey','datacqtr'] + [x for x in sorted(df.columns) if x not in ['gvkey','datacqtr']]
    df = df.reindex(col, axis=1)
    df.head(500).to_csv('check.csv')

    os.system("open -a '/Applications/Microsoft Excel.app' 'check.csv'")
    exit(0)

def convert_to_float32(df):

    ''''This def convert float64 to float32 to save memory usage.'''

    df.loc[:, df.dtypes == np.float64] = df.loc[:, df.dtypes == np.float64].astype(np.float32)
    df.loc[:, df.dtypes == np.int64] = df.loc[:, df.dtypes == np.int64].astype(np.int32)

def add_lag(df, lag_year): # df is TABLE main, lag_year for original model design is 5 years

    ''''1. This def adds lagging periods by given lag_year.'''

    print('---------------------------- (step 1/3) adding lag -----------------------------')
    start = time.time()

    convert_to_float32(df)

    if lag_year == 0:
        return df

    col = df.columns[2:]  # first three columns are gvkey, datacqtr(Timestamp), and sic which won't need lagging values

    lag_df = []  # create list for to be concated dataframes
    lag_df.append(df.dropna())  # current quarters, dropna remove records with missing important fields (e.g. niq....)

    for i in tqdm(range(lag_year*4-1)): # when lag_year is 5, here loop over past 19 quarter
        df_temp = df.groupby('gvkey').shift(i + 1)[col]
        df_temp.columns = ['{}_lag{}'.format(k, str(i+1).zfill(2)) for k in col] # name columns e.g. atq_lag01 -> last quarter total asset
        df_temp = df_temp.dropna(how='any')
        lag_df.append(df_temp)

    df_lag = pd.concat(lag_df, axis = 1, join='inner')

    end = time.time()
    print('(step 1/3) adding lag running time: {}'.format(end - start))
    print('after add lag: ', df_lag.shape)
    return df_lag

def merge_dep_macro(df, sql_version):

    ''''2. This def adds economic data (i.e. macro) & y.'''

    print('----------------- (step 2/3) adding macro & dependent variable -----------------')
    start = time.time()

    if sql_version is True: # sql version read TABLE from Postgre SQL
        db_string = 'postgres://postgres:DLvalue123@hkpolyu.cgqhw7rofrpo.ap-northeast-2.rds.amazonaws.com:5432/postgres'
        engine = create_engine(db_string)
        dep = pd.read_sql('SELECT * FROM niq_main', engine)
        macro = pd.read_sql("SELECT * FROM macro_main", engine)
        stock  = pd.read_sql("SELECT * FROM stock_main", engine)
    else: # local version read TABLE from local csv files -> faster
        macro = pd.read_csv('macro_main.csv')
        dep = pd.read_csv('niq_main.csv', usecols=['gvkey','datacqtr','niq','qoq','yoy','yoyr'])
        stock = pd.read_csv('stock_main.csv')
        print('local version running - niq, macro_main, stock_return')

    convert_to_float32(dep)
    convert_to_float32(macro)
    convert_to_float32(stock)

    dep['datacqtr'] = pd.to_datetime(dep['datacqtr'],format='%Y-%m-%d') # convert to timestamp
    macro['datacqtr'] = pd.to_datetime(macro['datacqtr'],format='%Y-%m-%d')
    stock['datacqtr'] = pd.to_datetime(stock['datacqtr'],format='%Y-%m-%d')

    merge_1 = pd.merge(stock, macro, on=['datacqtr'], how='left') # merge eco data & stock return by datacqtr
    merge_2 = pd.merge(merge_1, dep, on=['gvkey', 'datacqtr'], how='right') # add merge dependent variable

    merge_2 = merge_2.dropna(how='any') # remove records with missing eco data

    del merge_1, dep, macro, stock
    gc.collect()

    merge_3 = pd.merge(df, merge_2, on=['gvkey', 'datacqtr'], how='left')
    merge_3 = merge_3.dropna(how='any') # remove records with missing eco data

    end = time.time()
    print('(step 2/3) adding macro & dependent variable running time: {}'.format(end - start))
    print('after add macro & dependent variable : ', merge_3.shape)

    return merge_3

class clean_set:

    '''4. This def converts x -> std, y -> qcut'''

    def __init__(self, train, test):

        def divide_set(df): # this funtion cut main df into df for x_variables, y_yoy, y_qoq by columns position
            return df.iloc[:, 2:-4].values, df.iloc[:, -4].values, df.iloc[:, -3].values, df.iloc[:, -2].values, df.iloc[:, -1].values

        self.train_x, self.train_niq, self.train_qoq, self.train_yoy, self.train_yoyr = divide_set(train)
        print('train_x size before PCA: ', self.train_x.shape)

        try:
            self.test_x, self.test_niq, self.test_qoq, self.test_yoy, self.test_yoyr = divide_set(test) # can work without test set
        except:
            self.test_x=self.test_niq=self.test_qoq=self.test_yoy=self.test_yoyr=None

        self.pn_bins = [-np.inf, 0, np.inf]

    def standardize_x(self): # standardize x with train_x fit
        scaler = StandardScaler().fit(self.train_x)
        self.train_x = scaler.transform(self.train_x)

        try:
            self.test_x = scaler.transform(self.test_x) # can work without test set
        except:
            pass
        return self.train_x, self.test_x

    def y_qcut_unbalance(self, q, train_df, test_df):
        label_q = q
        while label_q > 0:
            try:
                train_df, cut_bins = pd.qcut(train_df, q=q, labels=range(label_q), retbins=True, duplicates='drop')
            except:
                label_q -= 1
                continue
        print('qcut labels:', set(train_df))
        print('y qcut label counts:', Counter(train_df))
        print('y qcut cut_bins:', cut_bins)

        try:
            test_df = pd.cut(test_df, bins=cut_bins, labels=range(label_q), duplicates='drop')  # can work without test set
            return train_df.astype(np.int8), test_df.astype(np.int8)
        except:
            return train_df.astype(np.int8), None

    def y_qcut(self, q, df_train, df_test):

        if q == 2:
            df_train, cut_bins = pd.cut(df_train, bins=self.pn_bins, labels=range(2), retbins=True)
        else:
            df_train, cut_bins = pd.qcut(df_train, q=q, labels=range(q), retbins=True)

        self.qcut={}
        print('qcut bins: ', cut_bins)
        d=dict(Counter(df_train))
        self.qcut['counts'] = list(d.values())

        self.qcut['cut_bins'] = list(cut_bins)

        try:
            df_test = pd.cut(df_test, bins=cut_bins, labels=range(q)) # can work without test set
            return df_train.astype(np.int8), df_test.astype(np.int8), self.qcut
        except:
            return df_train.astype(np.int8), None, self.qcut

    def qoq(self, q):
        return self.y_qcut(q, self.train_qoq, self.test_qoq)

    def yoy(self, q): # qcut y with train_y cut_bins
        return self.y_qcut(q, self.train_yoy, self.test_yoy)

    def yoyr(self, q): # qcut y with train_y cut_bins
        return self.y_qcut(q, self.train_yoyr, self.test_yoyr)

    def nom(self):

        df_train = pd.cut(self.train_niq, bins=self.pn_bins, labels=range(2))
        try:
            df_test = pd.cut(self.test_niq, bins=self.pn_bins, labels=range(2))
        except:
            df_test=None

        self.qcut = {}
        d=dict(Counter(df_train))
        self.qcut['counts'] = list(d.values())

        self.qcut['cut_bins'] = list(self.pn_bins)

        return df_train, df_test, self.qcut

def load_data(lag_year = 5, sql_version = False):

    '''This def consolidate steps 1 & 2 -> return big table with max(row) * max(col)'''

    # import engine, select variables, import raw database
    print('-------------- start load data into different sets (-> dictionary) --------------')
    start = time.time()

    if sql_version is True: # sql version read TABLE from Postgre SQL
        print('from postgres database')
        db_string = 'postgres://postgres:DLvalue123@hkpolyu.cgqhw7rofrpo.ap-northeast-2.rds.amazonaws.com:5432/postgres'
        engine = create_engine(db_string)
        main = pd.read_sql('SELECT * FROM main', engine)
    else: # local version read TABLE from local csv files -> faster
        main = pd.read_csv('main.csv')
        engine = None
        print('local version running - main')

    end = time.time()
    print('(step 0/3) read local csv - main - running time: {}'.format(end - start))

    main['datacqtr'] = pd.to_datetime(main['datacqtr'],format='%Y-%m-%d')

    # main = main.iloc[:,:4]

    # 1. add 20 lagging factors for each variable
    if lag_year != 0:
        main_lag = add_lag(main, lag_year)
    else:
        main_lag = main
        print('no lag')

    del main
    gc.collect()

    # 2. add dependent variable & macro variables to main
    main_lag = merge_dep_macro(main_lag, sql_version) # i.e. big table

    return main_lag.reset_index(drop=True) # i.e. big table

def train_test_clean(y_type, train, test = None, q=3): # y_type = ['yoy','qoq']; train, test(optional) are dataframes

    '''This def consolidate steps 4 -> return (train_x, test_x, train_y, test_y)'''

    main_period = clean_set(train, test) # create class
    train_x, test_x = main_period.standardize_x() # for x

    print('y_type using:',y_type)

    if y_type == 'yoy': # for y
        train_y, test_y, qcut = main_period.yoy(q=q)
    elif y_type == 'qoq':
        train_y, test_y, qcut = main_period.qoq(q=q)
    elif y_type == 'yoyr':
        train_y, test_y, qcut = main_period.yoyr(q=q)
    elif y_type == 'nom':
        train_y, test_y, qcut = main_period.nom()
    return train_x, test_x, train_y, test_y, qcut

def sample_from_datacqtr(df, y_type, testing_period, q, return_df=False): # df = big table; y_type = ['yoy','qoq']; testing_period are timestamp

    '''3.a. This def extract partial from big table with selected testing_period'''

    end = testing_period
    start = testing_period - relativedelta(years=20) # define training period

    train = df.loc[(start <= df['datacqtr']) & (df['datacqtr'] < end)]  # train df = 80 quarters
    test = df.loc[df['datacqtr'] == end]                                # test df = 1 quarter

    if return_df == True:
        return train_test_clean(y_type, train, test, q=q), train.iloc[:,:2]

    train_x, test_x, train_y, test_y, qcut = train_test_clean(y_type, train, test, q=q)
    qcut['date'] = testing_period.strftime('%Y-%m-%d')
    qcut['qcut'] = q
    qcut['y_type'] = y_type

    db_string = 'postgres://postgres:DLvalue123@hkpolyu.cgqhw7rofrpo.ap-northeast-2.rds.amazonaws.com:5432/postgres'
    engine = create_engine(db_string)

    pd.DataFrame.from_records([qcut]).to_sql('cut_bins', con=engine, if_exists='append')

    return train_x, test_x, train_y, test_y

def sample_from_main(df, y_type, part=5, q=3): # df = big table; y_type = ['yoy','qoq']; part = cut big table into how many parts

    '''3.b. This def extract partial from big table by random sampling'''

    df = shuffle(df) # shuffle big table

    part_len = len(df) // part # find length for each parts
    dfs = {}
    s = 0

    for i in range(part):
        set = df.iloc[s:(s + part_len)] # extract from big table
        train_x, test_x, train_y, test_y, qcut = train_test_clean(y_type, set, q=q) # here has no test set, only enter each set as training sets
        dfs[i] = (train_x, train_y)
        s += part_len

        del train_x, test_x, train_y, test_y
        gc.collect()

    return dfs

def trial_main():

    '''this code run above funtions for trail run'''

    # actual running scripts see def above -> for import
    import os
    os.chdir('/Users/Clair/PycharmProjects/HKP_ML_DL')

    # 1. return main dateframe
    main = load_data(lag_year=1)

    # 2.1 if want to return (train_x, test_x, train_y, test_y) by given testing_period
    train_x, test_x, train_y, test_y = sample_from_datacqtr(main, y_type = 'yoy', testing_period = dt.datetime(2008, 3, 31))
    print(train_x.shape, test_x.shape, train_y.shape, test_y.shape)

    exit(0)

    # 2.2 if want to return (train_x, train_y) by randomly sampled from main df
    '''dfs is dictionary contains all set of (train_x, train_y)'''
    dfs = sample_from_main(main, y_type = 'yoy',part = 3)

    for k in dfs.keys():
        x, y = dfs[k]
        print(type(x))
        print(type(y))
        print(y)

if __name__ == "__main__":
    import os
    # os.chdir('/Users/Clair/PycharmProjects/HKP_ML_DL/Hyperopt_LightGBM')

    main = load_data(lag_year=5, sql_version=False)
    print(main.shape)
    main[['gvkey','datacqtr']].to_csv('exist.csv', index=False)
    exit(0)
    a,b,c,d = sample_from_datacqtr(main, y_type='qoq', testing_period=dt.datetime(2008, 3, 31), q=2)
    print(a, b, c, d)
    print(main.isnull().sum().sum())
