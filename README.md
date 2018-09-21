# seer\_distro\_test

### Requires:
anaconda3/5.1.0

### Setup:

```
git clone https://github.com/SBU-BMI/seer_distro_test.git

cd seer_distro_test

conda env create -f environment.yml

source activate feature-env

```
Make sure you have folder `/data1/$USER/dataset` on a compute node where code will be executed.


### Compute patch-level nuclear feature results:
Remember to do `source activate feature-env`

Change coll\_name on line 830 to write to quip\_comp.[your\_collection\_name]

Run program **myscript.py**:

```
python myscript.py -s [name of slide] -u [user] -b [mongo host] -p [patch size]
```

### Validation

Modify `comparison_routines/script1.py`.
Update `case_id` and `db_host`.
To change the input database collection, change `input_collection`.
To change the output filename, change `output_file`.

There are 2 scripts to run:

```
cd comparison_routines/

python script1.py

python script2.py

```

**script1.py** calculates the difference, patch by patch, for the fields where we have a number value in both datasets.

**script2.py** takes the output csv from step 1, and calculates the max difference, the mean difference, and the standard deviation, and writes it to a file.

Again, if you want to change the input file, change `input_file`.  If you want to change the output file, change `output_file`.
