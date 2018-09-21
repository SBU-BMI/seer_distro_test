# seer\_distro\_test

### Requires:
anaconda3/5.1.0

### Setup:

```
git clone https://github.com/SBU-BMI/seer_distro_test.git

cd seer_distro_test

conda env create -f environment.yml

source activate feature-env

pip install --upgrade pip

conda install git

pip install openslide-python

```
Make sure you have folder `/data1/$USER/dataset` on a compute node where code will be executed.


### Compute patch-level nuclear feature results:
Remember to do `source activate feature-env`

Change coll\_name on line 830 to write to quip\_comp.[your\_collection\_name]

Run program:

```
python myscript.py -s [name of slide] -u [user] -b [mongo host] -p [patch size]
```